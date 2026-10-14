"""Focused saved-evidence gates; no engine, Torch or model is imported."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest

spec=importlib.util.spec_from_file_location("reaction_report",Path(__file__).parents[1]/"body_block_reaction_report.py")
report=importlib.util.module_from_spec(spec);spec.loader.exec_module(report)


def final(outcome="task_success", reason="melee_hit_blocked", hp=300, blocked=1, damage=0):
    return {"primary_entity_id":1,"game_ms":200,"outcome":outcome,"reason":reason,
            "actors":[{"entity_id":1,"hp":hp,"blocked_hits":blocked,"received_damage":damage}]}


def test_combat_kill_and_dodge_are_not_block_task_success():
    assert not report.mechanical_success(final("death_or_down","opponent_down",blocked=0),{"linked_blocked_hits":0})
    assert not report.mechanical_success(final("time_limit","episode_timeout",blocked=0),{"linked_blocked_hits":0})


@pytest.mark.parametrize("change", [{"hp":230},{"damage":70},{"blocked":0},
                                  {"reason":"opponent_down"},{"outcome":"time_limit"}])
def test_physical_block_gate_cannot_be_weakened(change):
    assert not report.mechanical_success(final(**change),{"linked_blocked_hits":1})


def test_counter_without_reversed_real_native_hit_has_no_block_credit():
    assert not report.mechanical_success(final(),{"linked_blocked_hits":0})
    assert report.mechanical_success(final(),{"linked_blocked_hits":1})


def credit_inputs():
    record={"policy":"gru","initial":{"primary_entity_id":1},"final":final(),"fallback_events":[]}
    runtime={"runtime_fallbacks":0,"declared_runtime_fallbacks":0,"healthy_fraction":1.0}
    reactions={"linked_reactions":[{"action_id":9}]}
    bc=SimpleNamespace(native_fallback_boundary=lambda initial,final,controls,mechanics:
                       {"restorations_before_final":[],"same_final_native_continuations":[]})
    return record,runtime,reactions,bc


def cut_inputs():
    identity={"entity_id":1,"session_epoch":2,"world_epoch":3,"control_generation":9}
    command={**identity,"cmd":"body_action","command_id":1,"action_id":9}
    refusal={"accepted":False,"reason":"stale_observation"}
    release={**identity,"cmd":"body_release","provider":"declared-provider"}
    receipt={"accepted":True,"reason":"release_pending"}
    row={**identity,"t_game_ms":16427,"observation_seq":7,"periodic":True}
    fallback={"kind":"model_fallback","reason":"realtime_decision_rejected:stale_observation","request":release,"response":receipt}
    event={**identity,"phase":"ownership","reason":"release_pending","release_reason":"released",
        "t_game_ms":16427,"wall_monotonic_ns":10250000000,"event_seq":15}
    pairs=[(command,refusal,9.,10.),(release,receipt,10.1,10.3),
           ({"cmd":"body_observations"},{"observations":[row]},10.4,10.5)]
    bc=SimpleNamespace(canonical=SimpleNamespace(identity=lambda value:tuple(value[k] for k in identity)),
        json_digest=lambda value:json.dumps(value,sort_keys=True))
    return {"fallback_events":[fallback]},[event],pairs,bc,row


def test_pending_same_generation_row_is_only_a_proved_failed_fragment_cut():
    record,events,pairs,bc,row=cut_inputs();cuts=report.command_fallback_cuts(record,events,pairs,bc)
    assert len(cuts)==1 and cuts[0]["quality_credit"] is False
    assert report.after_command_cut(row,cuts,pairs,bc)
    assert not report.after_command_cut({**row,"t_game_ms":16361},cuts,pairs,bc)
    assert not report.after_command_cut({**row,"observation_seq":8},cuts,pairs,bc)


def test_fragment_cut_requires_actual_release_ownership_receipt():
    record,_,pairs,bc,_=cut_inputs()
    with pytest.raises(ValueError,match="release ownership boundary"):
        report.command_fallback_cuts(record,[],pairs,bc)


def test_fragment_cut_cannot_rename_an_engine_refusal_or_borrow_earlier_pull():
    record,events,pairs,bc,row=cut_inputs();record["fallback_events"][0]["reason"]="realtime_decision_rejected:expired"
    with pytest.raises(ValueError,match="another engine refusal"):
        report.command_fallback_cuts(record,events,pairs,bc)
    record["fallback_events"][0]["reason"]="realtime_decision_rejected:stale_observation"
    cuts=report.command_fallback_cuts(record,events,pairs,bc)
    assert not report.after_command_cut(row,cuts,[(*pairs[-1][:2],9.,10.)],bc)


def test_mechanical_success_after_actual_native_restoration_has_no_model_credit():
    record,runtime,reactions,bc=credit_inputs()
    bc.native_fallback_boundary=lambda *args:{"restorations_before_final":[{"t_game_ms":199}],"same_final_native_continuations":[]}
    assert not report.model_credit(record,True,runtime,reactions,[],bc,[])


def test_same_final_native_execution_has_no_model_credit():
    record,runtime,reactions,bc=credit_inputs()
    bc.native_fallback_boundary=lambda *args:{"restorations_before_final":[],"same_final_native_continuations":[{"phase":"native_choice"}]}
    assert not report.model_credit(record,True,runtime,reactions,[],bc,[])


def test_normal_pending_terminal_cleanup_does_not_prove_native_execution():
    record,runtime,reactions,bc=credit_inputs()
    assert report.model_credit(record,True,runtime,reactions,[{"phase":"ownership","reason":"release_pending"}],bc,[])


@pytest.mark.parametrize("field,value",[("runtime_fallbacks",1),("declared_runtime_fallbacks",1),("healthy_fraction",.94)])
def test_unhealthy_model_cannot_receive_block_credit(field,value):
    record,runtime,reactions,bc=credit_inputs();runtime[field]=value
    assert not report.model_credit(record,True,runtime,reactions,[],bc,[])


def test_successful_real_jump_is_reported_separately_from_block():
    record,runtime,reactions,bc=credit_inputs();reactions["linked_reactions"]=[{"action_id":5}]
    assert not report.model_credit(record,True,runtime,reactions,[],bc,[])
    assert report.model_credit(record,True,runtime,reactions,[],bc,[],action=5)


def test_native_ai_success_is_never_neural_credit():
    record,runtime,reactions,bc=credit_inputs();record["policy"]="native"
    assert not report.model_credit(record,True,runtime,reactions,[],bc,[])


def test_api_binding_only_removes_transport_request_id():
    request={"cmd":"body_action","entity_id":1,"action_id":9,"observation_seq":5}
    assert report.same_request(request,{**request,"id":7})
    assert not report.same_request(request,{**request,"id":7,"observation_seq":6})


def order_fixture(tmp_path):
    archive=tmp_path/"source.tar.gz";files={"OpenGothic/common.cpp":b"one","OpenGothic/common/a.cpp":b"two"}
    with tarfile.open(archive,"w:gz") as output:
        for name,data in files.items():
            member=tarfile.TarInfo(name);member.size=len(data);output.addfile(member,io.BytesIO(data))
    def ordered(key):
        h=report.hashlib.sha256()
        for name in sorted(files,key=key):h.update(name.encode());h.update(report.hashlib.sha256(files[name]).digest())
        return h.hexdigest()
    lexical,native=ordered(lambda name:name),ordered(Path)
    assert lexical!=native
    declaration={"updated_runtime":{"source_sha256":lexical,"source_files_hashed":2}}
    manifest={"source_sha256":native,"source_files_hashed":2}
    failed=tmp_path/"failed.json";failed.write_text(json.dumps({"diagnostic_complete":False}))
    snapshot=tmp_path/"audit.json"
    snapshot.write_text(json.dumps({"source_provenance_correction":{"reason":"native_Path_component_order_vs_declaration_lexical_order",
        "original_failed_report":{"path":str(failed),"sha256":report.digest(failed)}}}))
    return archive,declaration,manifest,snapshot


def test_source_order_repair_preserves_old_declared_hash(tmp_path):
    archive,declaration,manifest,snapshot=order_fixture(tmp_path)
    evidence=report.source_order_evidence(archive,declaration,manifest,snapshot)
    assert evidence["declared_source_sha256"]==declaration["updated_runtime"]["source_sha256"]
    assert evidence["actual_native_Path_order_source_sha256"]==manifest["source_sha256"]
    assert evidence["native_source_bytes_changed"] is False


def test_source_order_repair_does_not_admit_unrelated_code_hash(tmp_path):
    archive,declaration,manifest,snapshot=order_fixture(tmp_path)
    declaration["updated_runtime"]["source_sha256"]="0"*64
    with pytest.raises(ValueError,match="not the proved"):
        report.source_order_evidence(archive,declaration,manifest,snapshot)


def test_source_order_repair_requires_original_failure_unchanged(tmp_path):
    archive,declaration,manifest,snapshot=order_fixture(tmp_path)
    (tmp_path/"failed.json").write_text(json.dumps({"diagnostic_complete":True}))
    with pytest.raises(ValueError,match="audit failure missing/changed"):
        report.source_order_evidence(archive,declaration,manifest,snapshot)


def test_source_order_repair_does_not_allow_actual_manifest_code_drift(tmp_path):
    archive,declaration,manifest,snapshot=order_fixture(tmp_path)
    manifest["source_sha256"]="0"*64
    with pytest.raises(ValueError,match="captured native source differs"):
        report.source_order_evidence(archive,declaration,manifest,snapshot)
