"""Pure binding tests; no model, native runner or service is instantiated."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

MODULE_PATH = Path(__file__).parents[1] / "body_evaluation_repair.py"
spec = importlib.util.spec_from_file_location("body_evaluation_repair", MODULE_PATH)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def test_noncache_source_mutation_fails_frozen_environment(tmp_path):
    code = tmp_path / "code/helper.py"
    code.parent.mkdir(); code.write_text("physical_mask = True\n")
    runner = tmp_path / "reference/gothic-body-runner"
    runner.parent.mkdir(); runner.write_bytes(b"runner")
    archive = tmp_path / "source.tar.gz"; archive.write_bytes(b"archive")
    environment = {"frozen_files": {"code/helper.py": repair.sha(code)},
                   "binary_sha256": repair.sha(runner), "source_archive_sha256": repair.sha(archive)}
    (tmp_path / "environment.json").write_text(json.dumps(environment))
    repair.frozen_environment(tmp_path)
    code.write_text("physical_mask = False\n")
    with pytest.raises(ValueError, match="artifact bytes changed"):
        repair.frozen_environment(tmp_path)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "code/../../outside"])
def test_inventory_rejects_path_escape(tmp_path, name):
    with pytest.raises(ValueError, match="unsafe artifact path"):
        repair.relative_file(tmp_path, name)


def test_inventory_rejects_external_symlink(tmp_path):
    root = tmp_path / "run"; root.mkdir()
    external = tmp_path / "other.py"; external.write_text("source")
    (root / "helper.py").symlink_to(external)
    with pytest.raises(ValueError, match="escaped artifact"):
        repair.relative_file(root, "helper.py")


def test_source_archive_digest_matches_native_provenance(tmp_path):
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in [("OpenGothic/z.cpp", b"z"), ("scripts/irrelevant.py", b"script"),
                           ("OpenGothic/a.cpp", b"a")]:
            member = tarfile.TarInfo(name); member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    h = repair.hashlib.sha256()
    for name, data in [("OpenGothic/a.cpp", b"a"), ("OpenGothic/z.cpp", b"z")]:
        h.update(name.encode()); h.update(repair.hashlib.sha256(data).digest())
    assert repair.archive_source_identity(path) == {"source_sha256": h.hexdigest(), "source_files_hashed": 2}


def test_source_digest_uses_path_components_not_flat_string_order(tmp_path):
    path=tmp_path/"source.tar.gz"
    files={"OpenGothic/common.cpp":b"file","OpenGothic/common/a.cpp":b"nested"}
    with tarfile.open(path,"w:gz") as archive:
        for name,data in files.items():
            member=tarfile.TarInfo(name);member.size=len(data);archive.addfile(member,io.BytesIO(data))
    expected=repair.hashlib.sha256()
    for name in sorted(files,key=Path):
        expected.update(name.encode());expected.update(repair.hashlib.sha256(files[name]).digest())
    lexical=repair.hashlib.sha256()
    for name in sorted(files):
        lexical.update(name.encode());lexical.update(repair.hashlib.sha256(files[name]).digest())
    assert expected.hexdigest()!=lexical.hexdigest()
    assert repair.archive_source_identity(path)["source_sha256"]==expected.hexdigest()


def profile():
    return json.loads((MODULE_PATH.parents[1] / "evals/npc-body/body-block-reaction-v1.json").read_text())


def test_current_known_profile_is_diagnostic_only():
    repair.diagnostic_profile(profile())


@pytest.mark.parametrize("field,value", [("scope", "screening"), ("original_v6_credit", True)])
def test_profile_cannot_replace_old_failure_or_become_screening(field, value):
    config = profile(); config[field] = value
    with pytest.raises(ValueError, match="invalid diagnostic scope"):
        repair.diagnostic_profile(config)


def test_profile_cannot_weaken_physical_hp_gate():
    config = profile(); config["mechanical_success"]["primary_hp"] = 230
    with pytest.raises(ValueError, match="physical block gate weakened"):
        repair.diagnostic_profile(config)


def test_profile_cannot_add_preparation_history():
    config = profile(); config["runtime"]["preparation_hidden_updates"] = 1
    with pytest.raises(ValueError, match="sampling protocol changed"):
        repair.diagnostic_profile(config)
