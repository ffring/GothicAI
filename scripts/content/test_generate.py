"""Set recipes, physical levels and retained retries without loading FLUX."""
from pathlib import Path
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
