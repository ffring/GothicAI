"""Separate CPU verification/CUDA training profiles; no numeric model runs."""
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS=Path(__file__).parents[1]
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location("device_profile_bc_report",SCRIPTS/"body_bc_report.py")
bc=importlib.util.module_from_spec(spec);spec.loader.exec_module(bc)


class VerificationReached(Exception):
    pass


def fixture(monkeypatch, training_device):
    index={"feature_version":"numeric","action_version":1,"model_sampling":{},"groups":{},"episodes":[]}
    dataset={"index":index,"normalizer":{"feature_names":["x"]}}
    provenance={"dataset_sha256":"bound","normalization_sha256":"bound","dataset_code_sha256":"bound",
                **{key:index[key] for key in ("feature_version","action_version","model_sampling","groups")},"train_episode_ids":[]}
    verify={"kind":"body-bc-verification","architecture":"gru","dataset":provenance,"device":"cpu","threads":2,
            "step_sequence_max_error":0,"step_sequence_state_max_error":0,"reset_max_error":0,"actor_isolation_max_error":0,
            "invalid_probability_max":0,"step_latency_ms":{"warmup":1,"samples":1,"p95":1}}
    def read(path):
        if path.name=="verify.json":return verify
        raise VerificationReached("all numeric verification gates passed")
    monkeypatch.setattr(bc,"digest",lambda path:"bound")
    monkeypatch.setattr(bc,"read_json",read)
    config={"training":{"device":training_device,"threads":2},"verification":{"tolerance":1e-5,"warmup":1,"iterations":1,
            "model_step_p95_budget_ms":5}}
    return config,dataset,verify


def test_default_verification_device_keeps_existing_cpu_behavior(monkeypatch,tmp_path):
    config,dataset,_=fixture(monkeypatch,"cpu")
    with pytest.raises(VerificationReached):bc.model_evidence(tmp_path,"gru",config,dataset)


def test_explicit_cpu_verification_accepts_cuda_training_profile_without_rewriting_artifacts(monkeypatch,tmp_path):
    config,dataset,verify=fixture(monkeypatch,"cuda")
    with pytest.raises(VerificationReached):bc.model_evidence(tmp_path,"gru",config,dataset,verification_device="cpu")
    assert verify["device"]=="cpu" and config["training"]["device"]=="cuda"


def test_default_does_not_silently_accept_cpu_verification_for_cuda_training(monkeypatch,tmp_path):
    config,dataset,_=fixture(monkeypatch,"cuda")
    with pytest.raises(ValueError,match="numerical verification schema/provenance differs"):
        bc.model_evidence(tmp_path,"gru",config,dataset)


def test_wrong_explicit_verification_device_fails(monkeypatch,tmp_path):
    config,dataset,_=fixture(monkeypatch,"cpu")
    with pytest.raises(ValueError,match="numerical verification schema/provenance differs"):
        bc.model_evidence(tmp_path,"gru",config,dataset,verification_device="cuda")
