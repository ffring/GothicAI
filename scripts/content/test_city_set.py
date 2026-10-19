"""City-3 retains all tool calls while admitting independent staged batches."""
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

import city_set


ROOT = Path(__file__).resolve().parents[2]
MAPPING = ROOT/'data/content/city-materials.csv'
BIBLE = ROOT/'docs/content-style.md'
GROUND = 'NW_CITY_DIRTYGROUND_02.TGA'


def mapping_with_targets(tmp_path):
    with MAPPING.open(newline='') as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        row['target_luminance'] = '.18'
    path = tmp_path/'mapping.csv'
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def source(tmp_path, texture=GROUND, number=1, prompt=None, colour=(120, 100, 80)):
    staging, store = tmp_path/'staging', tmp_path/'raw'
    relative = Path('city-3')/f'{Path(texture).stem}-attempt-{number}.png'
    path = store/relative
    path.parent.mkdir(parents=True, exist_ok=True)
    # Small input is enough to test source retention; CPU filters are stubbed.
    Image.new('RGB', (16, 16), colour).save(path)
    metadata = dict(texture=texture, attempt=number, prompt=prompt or 'actual tool prompt',
        utc='2026-10-19T01:02:03Z', seconds=1.25, source=relative.as_posix(),
        tool='image_gen.imagegen')
    meta = staging/'sources'/f'{Path(texture).stem}-attempt-{number}.json'
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps(metadata)+'\n')
    return staging, store, path, meta


@pytest.fixture
def fast_filter(monkeypatch):
    def maps(image, record):
        values = {name: np.full((16, 16, 3 if name in ('albedo', 'normal') else 1),
            .5, dtype=np.float32).squeeze() for name in city_set.bakeoff.MAPS}
        return values, dict(saturation=dict(mean_hsv_s_after=.1)), None
    monkeypatch.setattr(city_set.generate, 'target_corrected_maps', maps)
    monkeypatch.setattr(city_set.seam_fix, 'seamless',
        lambda image, axes, overlap_fraction: (image, dict(axes=axes, overlap_fraction=overlap_fraction)))


def registry_file(tmp_path, mapping, textures=(GROUND,)):
    path = tmp_path/'registry.jsonl'
    rows = city_set.recipes(mapping, BIBLE, textures=textures)
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    return path


def test_recipes_are_complete_text_only_and_do_not_mutate_mapping(tmp_path):
    mapping = mapping_with_targets(tmp_path)
    before = mapping.read_bytes()
    rows = city_set.recipes(mapping, BIBLE)
    assert len(rows) == len({row['replaces'][0] for row in rows}) == 52
    assert mapping.read_bytes() == before
    for row in rows:
        assert row['pipeline'] == 'imagegen-filter' and row['seed'] is None
        assert city_set.NO_BAKED_LIGHT in row['prompt']
        params = row['parameters']
        assert params['target_luminance'] == .18
        assert params['metallic'] == 0
        assert params['input'] == 'text only; no original pixels or game screenshots'
        if params['decision'] != 'include':
            assert params['scale'] == 1 and params['antitile'] == 'off'
            assert params['physical_uv_metres'][0]/params['physical_uv_metres'][1] == params['source_aspect']
    gable = next(row for row in rows if row['replaces'] == ['NW_CITY_HAFEN_GIEBEL_01.TGA'])
    assert 'horizontal overlapping' in gable['prompt']
    assert 'vertical' not in gable['prompt']


def test_targets_required_for_import_but_not_for_prompt_preparation(tmp_path):
    mapping = mapping_with_targets(tmp_path)
    mapping.write_text(mapping.read_text().replace(',0.18\n', ',\n').replace(',.18\n', ',\n'))
    with pytest.raises(ValueError, match='requires target_luminance'):
        city_set.recipes(mapping, BIBLE)
    assert len(city_set.recipes(mapping, BIBLE, require_target=False)) == 52


def test_partial_batch_import_retains_source_and_preserves_other_pending_rows(tmp_path, fast_filter):
    mapping = mapping_with_targets(tmp_path)
    other = 'NW_CITY_WALL_03.TGA'
    registry = registry_file(tmp_path, mapping, (GROUND, other))
    before = json.loads(registry.read_text().splitlines()[1])
    staging, store, raw, metadata = source(tmp_path)
    records = city_set.import_batch(staging, registry, textures=[GROUND],
        mapping=mapping, bible=BIBLE, source_root=store)
    assert len(records) == 1
    entries = city_set.registry_module.check_registry(registry, source_root=store)
    assert entries[1] == before and entries[1]['generation']['outcome'] == 'pending'
    attempt = records[0]['generation']['attempts'][0]
    assert attempt['source_sha256'] == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert metadata.with_suffix('.png').is_symlink()
    assert not Path(metadata.with_suffix('.png').readlink()).is_absolute()
    assert records[0]['generation']['seam_fix']['overlap_fraction'] == 1/8
    city_set.registry_module.validate_entry(records[0], content_root=staging/'sources', source_root=store)


def test_all_attempts_are_retained_and_prior_source_cannot_be_rewritten(tmp_path, fast_filter):
    mapping = mapping_with_targets(tmp_path)
    registry = registry_file(tmp_path, mapping)
    staging, store, raw, metadata = source(tmp_path)
    initial = city_set.import_batch(staging, registry, textures=[GROUND], mapping=mapping, bible=BIBLE, source_root=store)[0]
    selected_files = {relative: (staging/'sources'/relative).read_bytes() for relative in initial['files'].values()}
    source(tmp_path, number=2, colour=(140, 110, 90))
    updated = city_set.import_batch(staging, registry, textures=[GROUND], mapping=mapping, bible=BIBLE,
        source_root=store, selections={GROUND: 1}, reviews={GROUND: 'first attempt preferred'})[0]
    assert len(updated['generation']['attempts']) == 2
    assert updated['generation']['selected_attempt'] == 1
    assert all((staging/'sources'/relative).read_bytes() == value for relative, value in selected_files.items())
    changed = json.loads(metadata.read_text())
    changed['prompt'] = 'rewritten history'
    metadata.write_text(json.dumps(changed))
    previous_registry = registry.read_bytes()
    with pytest.raises(ValueError, match='cannot be removed or rewritten'):
        city_set.import_batch(staging, registry, textures=[GROUND], mapping=mapping, bible=BIBLE, source_root=store)
    assert registry.read_bytes() == previous_registry


def test_attempt_sequence_and_transparency_are_rejected(tmp_path):
    staging, store, raw, metadata = source(tmp_path, number=2)
    with pytest.raises(ValueError, match='consecutive attempts'):
        city_set.read_sources(staging/'sources', [GROUND], store)
    metadata.unlink()
    staging, store, raw, metadata = source(tmp_path)
    Image.new('RGBA', (16, 16), (100, 80, 60, 0)).save(raw)
    with pytest.raises(ValueError, match='must be opaque'):
        city_set.read_sources(staging/'sources', [GROUND], store)


def test_prepare_preserves_already_retained_pending_attempt(tmp_path):
    mapping = mapping_with_targets(tmp_path)
    registry = registry_file(tmp_path, mapping)
    records = [json.loads(registry.read_text())]
    records[0]['generation'].update(attempts=[dict(number=1, prompt='actual tool prompt')],
        reason='waiting for a superseded decision')
    registry.write_text(json.dumps(records[0])+'\n')
    updated = city_set.prepare(registry, mapping, BIBLE, tmp_path/'raw')
    retained = next(row for row in updated if row['replaces'] == [GROUND])
    assert retained['generation']['attempts'] == records[0]['generation']['attempts']
    assert 'reason' not in retained['generation']
    assert len(updated) == 52


def test_failed_selection_requires_review_and_modules_use_narrow_overlap(tmp_path, fast_filter):
    mapping = mapping_with_targets(tmp_path)
    texture = 'NW_CITY_WOODBEAM_02.TGA'
    staging, store, _, _ = source(tmp_path, texture=texture)
    record = city_set.recipes(mapping, BIBLE, textures=[texture])[0]
    attempts = city_set.read_sources(staging/'sources', [texture], store)[texture]
    with pytest.raises(ValueError, match='requires a review note'):
        city_set.import_material(record, attempts, staging/'sources', store, failed=True)
    imported = city_set.import_material(record, attempts, staging/'sources', store,
        failed=True, review='best of three, contact shading still visible')
    assert imported['generation']['selected_failed']
    assert imported['generation']['seam_fix'] == dict(axes='v', overlap_fraction=1/32)


def test_reuse_keeps_raw_provenance_without_new_tool_call(tmp_path, fast_filter):
    mapping = mapping_with_targets(tmp_path)
    registry = registry_file(tmp_path, mapping)
    old_stage, store, raw, _ = source(tmp_path)
    record = city_set.import_batch(old_stage, registry, textures=[GROUND], mapping=mapping,
        bible=BIBLE, source_root=store)[0]
    original = deepcopy(record)
    original['id'] = original['id'].replace('city-3-', 'city-2-imagegen-', 1)
    original['parameters']['set'] = 'city-2-imagegen'
    registry.write_text(json.dumps(original)+'\n')
    new_stage = tmp_path/'city-3'
    reused = city_set.reuse(new_stage, old_stage, registry, textures=[GROUND], mapping=mapping,
        bible=BIBLE, source_root=store)[0]
    assert reused['generation']['reused_from'] == original['id']
    assert reused['generation']['new_tool_calls'] == 0
    assert reused['generation']['attempts'] == original['generation']['attempts']
    assert city_set.registry_module.source_references(reused) == city_set.registry_module.source_references(original)
    assert any(path.is_symlink() for path in (new_stage/'sources/raw-sources').rglob('*.png'))


def test_cli_import_runs_backup_only_after_registry_was_written(tmp_path, fast_filter, monkeypatch):
    mapping = mapping_with_targets(tmp_path)
    registry = registry_file(tmp_path, mapping)
    staging, store, _, _ = source(tmp_path)
    calls = []
    def backup(command, check):
        assert city_set.registry_module.check_registry(registry, source_root=store)[0]['generation']['outcome'] == 'complete'
        calls.append(command)
    monkeypatch.setattr(city_set.subprocess, 'run', backup)
    assert city_set.main(['import', '--registry', str(registry), '--mapping', str(mapping),
        '--bible', str(BIBLE), '--source-root', str(store), '--staging', str(staging), '--texture', GROUND]) == 0
    assert len(calls) == 1 and calls[0][0].endswith('backup-sources.sh')
