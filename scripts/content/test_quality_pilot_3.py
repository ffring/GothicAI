"""Synthetic IID provenance gates and protection of earlier staging sets."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

import quality_pilot_3


def test_frozen_prepared_png_preserves_exact_iid_bytes_across_encoders(tmp_path):
    target = tmp_path / 'input.png'
    image = Image.new('RGB', (32, 32), '#607080')
    image.save(target, compress_level=0)
    before = target.read_bytes()
    digest = quality_pilot_3.prepared_input(image, target, frozen=True)
    assert digest == quality_pilot_3.pilot.file_digest(target)
    assert target.read_bytes() == before
    changed = image.copy()
    changed.putpixel((0, 0), (1, 2, 3))
    with pytest.raises(ValueError, match='rebuilt pixels differ'):
        quality_pilot_3.prepared_input(changed, target, frozen=True)
    assert target.read_bytes() == before


@pytest.fixture
def iid_input(tmp_path):
    prepared = tmp_path / 'prepared' / 'pilot-3a'
    prepared.mkdir(parents=True)
    source = prepared / 'NW_CITY_ROOF_01.png'
    Image.new('RGB', (32, 32), '#606060').save(source)
    digest = quality_pilot_3.pilot.file_digest(source)
    record = dict(replaces=['NW_CITY_ROOF_01.TGA'], parameters=dict(set='pilot-3a'),
        models=[], generation=dict(detector_before=dict(lighting_risk=True), prepared_sha256=digest))
    args = SimpleNamespace(prepared_dir=prepared.parent, source_root=tmp_path / 'sources')
    return record, args, source


def test_unflagged_material_does_not_consult_iid_candidates(iid_input):
    record, args, source = iid_input
    record['generation']['detector_before']['lighting_risk'] = False

    class UnreadableCandidates(dict):
        def get(self, key):
            pytest.fail('unflagged material consulted IID outputs')

    # No model output directory or source-root argument is needed at this gate.
    result = quality_pilot_3.apply_iid(record,
        SimpleNamespace(prepared_dir=args.prepared_dir), UnreadableCandidates())
    with Image.open(source) as original:
        assert np.array_equal(result, np.asarray(original))
    assert record['generation']['marigold_applied'] is False
    assert record['models'] == []
    assert not args.source_root.exists()


@pytest.mark.parametrize('candidate_sha', [None, 'b' * 64])
def test_flagged_material_requires_exact_prepared_sha(iid_input, candidate_sha):
    record, args, _ = iid_input
    candidates = {} if candidate_sha is None else {candidate_sha: (Path('unreadable.png'), {}, True)}
    with pytest.raises(ValueError, match='no IID output for exact prepared SHA'):
        quality_pilot_3.apply_iid(record, args, candidates)
    assert record['generation']['marigold_applied'] is False
    assert not args.source_root.exists()


def test_exact_prepared_sha_can_reuse_iteration2_iid(iid_input, tmp_path):
    record, args, source = iid_input
    args.iteration2_marigold_dir = tmp_path / 'old-iid'
    args.iteration2_marigold_dir.mkdir()
    # The old texture filename deliberately differs: reuse depends on bytes.
    output = args.iteration2_marigold_dir / 'iteration2-different-name-albedo.png'
    Image.new('RGB', (32, 32), '#a0a0a0').save(output)
    item = dict(input=str(source), output=str(output),
        source_sha256=record['generation']['prepared_sha256'],
        output_sha256=quality_pilot_3.pilot.file_digest(output), seconds=2.5)
    args.iteration2_marigold_log = tmp_path / 'old.jsonl'
    args.iteration2_marigold_log.write_text(json.dumps(item) + '\n')
    args.marigold_dir = tmp_path / 'new-iid'
    args.marigold_log = tmp_path / 'new.jsonl'

    result = quality_pilot_3.apply_iid(record, args, quality_pilot_3.iid_candidates(args))

    assert result.shape == (32, 32, 3)
    assert result.dtype == np.uint8
    assert np.all(result > 96)
    generation = record['generation']
    assert generation['marigold_applied'] is True
    assert generation['iid_reused_iteration2'] is True
    assert generation['iid_seconds'] == 2.5
    assert generation['iid_source']['source_sha256'] == item['output_sha256']
    retained = args.source_root / generation['iid_source']['source_png']
    assert retained.read_bytes() == output.read_bytes()
    assert record['models'][0]['id'] == quality_pilot_3.marigold_iid.MODEL_ID


def output_args(tmp_path):
    return SimpleNamespace(staging_parent=tmp_path / 'staging', city3=tmp_path / 'staging/city-3',
        source_root=tmp_path / 'sources', prepared_dir=tmp_path / 'prepared', derived_root=tmp_path / 'derived',
        report_dir=tmp_path / 'report', registry_output=tmp_path / 'quality-pilot-3.jsonl',
        base_registry=tmp_path / 'materials.jsonl', iteration2_registry=tmp_path / 'quality-pilot-2.jsonl')


@pytest.mark.parametrize('protected', ['city-3', 'pilot-2', 'pilot-b', 'pilot-c'])
@pytest.mark.parametrize('destination', ['source_root', 'prepared_dir', 'derived_root', 'report_dir', 'registry_output'])
@pytest.mark.parametrize('nested', [False, True])
def test_outputs_cannot_overwrite_earlier_staging_sets(tmp_path, protected, destination, nested):
    args = output_args(tmp_path)
    base = args.staging_parent / protected
    setattr(args, destination, base / 'new-output' if nested else base)
    with pytest.raises(ValueError, match='overlaps a protected staging set'):
        quality_pilot_3.validate_outputs(args)
    assert not args.staging_parent.exists()


@pytest.mark.parametrize('registry', ['base_registry', 'iteration2_registry'])
def test_registry_output_cannot_replace_an_earlier_registry(tmp_path, registry):
    args = output_args(tmp_path)
    args.registry_output = getattr(args, registry)
    with pytest.raises(ValueError, match='iteration-3 registry must be separate'):
        quality_pilot_3.validate_outputs(args)
    assert not args.registry_output.exists()


def test_separate_iteration3_outputs_are_allowed_without_creating_them(tmp_path):
    quality_pilot_3.validate_outputs(output_args(tmp_path))
    assert not list(tmp_path.iterdir())
