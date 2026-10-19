"""Set recipes, physical levels and retained retries without loading FLUX."""
from pathlib import Path
from copy import deepcopy
import sys

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import generate
import registry

ROOT = Path(__file__).resolve().parents[2]


def recipes():
    return generate.recipes(ROOT/'data/content/city-materials.csv', ROOT/'docs/content-style.md', 'city-1')


def test_real_inputs_cover_includes_with_unique_pinned_text_recipes():
    records = recipes()
    assert len(records) == 36
    assert len({r['seed'] for r in records}) == len({r['id'] for r in records}) == 36
    for record in records:
        registry.validate_entry(record)
        assert record['parameters']['state'] in record['prompt']
        assert '<' not in record['prompt']
        assert record['parameters']['steps'] == 4
        assert record['parameters']['guidance_scale'] == 0
        assert record['models'][0]['revision'] == generate.bakeoff.FLUX_REVISION


def test_prepare_preserves_progress_and_detects_changed_inputs(tmp_path):
    path = tmp_path/'registry.jsonl'
    mapping, bible = ROOT/'data/content/city-materials.csv', ROOT/'docs/content-style.md'
    records = generate.prepare(path, mapping, bible, 'city-1')
    records[0]['generation']['attempts'].append({'seed': 1, 'outcome': 'rejected'})
    generate.bakeoff.write_registry(path, records)
    assert generate.prepare(path, mapping, bible, 'city-1')[0]['generation']['attempts']
    copy = tmp_path/'style.md'
    copy.write_text(bible.read_text().replace('very unlikely string', 'unused')+'\n')
    with pytest.raises(ValueError, match='inputs changed'):
        generate.prepare(path, mapping, copy, 'city-1')


@pytest.mark.parametrize('colour', [(30, 24, 20), (230, 220, 200), (110, 95, 75)])
def test_level_correction_and_quantized_maps_obey_class_ranges(colour):
    for record in recipes():
        maps, correction, rejection = generate.corrected_maps(Image.new('RGB', (32, 32), colour), record)
        lo, hi = record['parameters']['luminance_range']
        assert lo <= correction['linear_mean_after'] <= hi
        assert rejection is None
        rlo, rhi = record['parameters']['roughness_range']
        stored = np.rint(maps['roughness']*255)/255
        assert stored.min() >= rlo and stored.max() <= rhi
        assert np.all(maps['metallic'] == 0)


def test_retry_preserves_rejected_attempt_and_enforces_three_attempts():
    record = recipes()[0]
    record['generation'] = dict(outcome='complete', attempts=[dict(seed=record['seed'], outcome='complete', files={'source': 'tile.png'})])
    record['files'] = {'albedo': 'tile.png'}
    generate.reject([record], record['id'], 'looks unlike floorboards')
    assert record['generation']['attempts'][0]['files'] == {'source': 'tile.png'}
    assert record['generation']['attempts'][0]['outcome'] == 'rejected'
    assert record['files'] == {}
    assert record['seed'] != generate.seed_for(record['replaces'][0], 2)
    record['generation'].update(outcome='complete', attempts=[{}]*3)
    with pytest.raises(ValueError, match='three attempts exhausted'):
        generate.reject([record], record['id'], 'wrong class')


def test_black_source_is_rejected_instead_of_claiming_valid_colour():
    _, _, rejection = generate.corrected_maps(Image.new('RGB', (32, 32), 'black'), recipes()[0])
    assert rejection == 'brightness correction could not reach class range'


def test_prompt_rules_and_saturation_thresholds_come_from_bible():
    for record in recipes():
        cls = record['parameters']['material_class']
        assert record['parameters']['saturation_limit'] == (.4 if cls == 'roof_tiles' else .3)
        assert 'evenly distributed small details, no large isolated features' in record['prompt']
        if cls in ('mud', 'dirt_path'):
            assert 'grime in the cracks' not in record['prompt']
            assert 'continuous' in record['prompt']


def test_explicit_refresh_preserves_history_and_grants_only_three_new_attempts(tmp_path):
    records = recipes()
    target = records[0]
    for record in records:
        record['parameters'].pop('saturation_limit')
        record['prompt'] = 'previous real prompt'
        record['files'] = {key: 'old/'+key+'.png' for key in generate.bakeoff.MAPS}
        record['generation'] = dict(outcome='complete', attempts=[dict(number=n, seed=n, outcome='complete', files=deepcopy(record['files'])) for n in (1,2,3)])
    old = deepcopy(records)
    mapping, bible = ROOT/'data/content/city-materials.csv', ROOT/'docs/content-style.md'
    generate.refresh(records, mapping, bible, 'city-1', target['replaces'], 'user revised prompt')
    assert target['generation']['attempt_limit'] == 6
    assert target['generation']['series_start'] == 3
    assert target['generation']['outcome'] == 'pending' and target['files'] == {}
    assert target['generation']['recipes'][0]['prompt'] == 'previous real prompt'
    assert len(target['generation']['recipes']) == 2
    assert all(a['recipe_revision']==1 for a in target['generation']['attempts'])
    assert target['generation']['previous_selections'][0]['files'] == old[0]['files']
    assert records[1]['prompt'] == old[1]['prompt'] and records[1]['files'] == old[1]['files']
    assert records[1]['generation']['outcome'] == 'complete'
    snapshot = deepcopy(records)
    generate.refresh(records, mapping, bible, 'city-1', target['replaces'], 'repeat')
    assert records == snapshot
    path = tmp_path/'registry.jsonl'
    generate.bakeoff.write_registry(path, records)
    assert generate.prepare(path, mapping, bible, 'city-1') == records


def test_postprocess_changes_only_albedo_and_is_idempotent(tmp_path):
    record = recipes()[0]
    record['generation'].update(outcome='complete', prepared_inputs='a'*64)
    record['files'] = {key: 'source/'+key+'.png' for key in generate.bakeoff.MAPS}
    source = tmp_path/'out/source'
    source.mkdir(parents=True)
    for key in generate.bakeoff.MAPS:
        Image.new('RGB', (16,16), (180,40,12)).save(source/(key+'.png'))
    before = deepcopy(record)
    old_bytes = {p.name:p.read_bytes() for p in source.iterdir()}
    generate.postprocess([record], tmp_path, 'city-1')
    assert record['files']['albedo'] != before['files']['albedo']
    assert all(record['files'][k] == before['files'][k] for k in generate.bakeoff.MAPS if k!='albedo')
    assert record['seed']==before['seed'] and record['prompt']==before['prompt']
    assert old_bytes == {p.name:p.read_bytes() for p in source.iterdir()}
    assert record['generation']['saturation_correction']['mean_hsv_s_after'] <= .3
    snapshot = deepcopy(record)
    generate.postprocess([record], tmp_path, 'city-1')
    assert record == snapshot


def test_refresh_other_texture_after_acknowledging_its_postprocess_inputs(tmp_path):
    records = recipes()[:2]
    for record in records:
        record['prompt'] = 'previous real prompt'
        record['parameters'].pop('saturation_limit')
        record['files'] = {key: 'old/'+key+'.png' for key in generate.bakeoff.MAPS}
        record['generation'] = dict(outcome='complete', attempts=[dict(
            number=n, seed=n, outcome='complete', files=deepcopy(record['files'])) for n in (1, 2, 3)])
    mapping, bible = ROOT/'data/content/city-materials.csv', ROOT/'docs/content-style.md'
    generate.refresh(records, mapping, bible, 'city-1', records[0]['replaces'], 'revise first')
    first = deepcopy(records[0])
    second = deepcopy(records[1])
    assert second['generation']['prepared_inputs']
    assert 'attempt_limit' not in second['generation']
    # The same distinction must survive saving/loading between separate CLI calls.
    path = tmp_path/'registry.jsonl'
    generate.bakeoff.write_registry(path, records)
    records = generate.bakeoff.read_registry(path)
    generate.refresh(records, mapping, bible, 'city-1', records[1]['replaces'], 'revise second')
    assert records[0] == first
    refreshed = records[1]
    assert refreshed['prompt'] == recipes()[1]['prompt']
    assert refreshed['generation']['outcome'] == 'pending'
    assert refreshed['generation']['attempt_limit'] == 6
    assert refreshed['generation']['series_start'] == 3
    assert refreshed['files'] == {}
    assert refreshed['generation']['previous_selections'][0]['files'] == second['files']
    assert refreshed['generation']['recipes'][0]['prompt'] == second['prompt']
    assert len(refreshed['generation']['recipes']) == 2
    snapshot = deepcopy(records)
    for record in records:
        generate.refresh(records, mapping, bible, 'city-1', record['replaces'], 'repeat')
    assert records == snapshot


def test_refresh_preserves_budget_already_granted_by_older_generator():
    records = recipes()[:2]
    for record in records:
        record['prompt'] = 'previous real prompt'
    mapping, bible = ROOT/'data/content/city-materials.csv', ROOT/'docs/content-style.md'
    generate.refresh(records, mapping, bible, 'city-1', records[0]['replaces'], 'previous generator')
    del records[0]['generation']['refreshed_inputs']
    snapshot = deepcopy(records)
    generate.refresh(records, mapping, bible, 'city-1', records[0]['replaces'], 'repeat after upgrade')
    assert records == snapshot
