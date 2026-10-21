import json

import numpy as np
import pytest

import building_set
from material_colour import _linear
import remaster


def noise(seed, mean, size=64):
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(mean, 18, (size, size, 3)), 0, 255).astype(np.uint8)


def test_mask_outside_excludes_rectangle():
    mask = building_set.mask_outside((100, 200), [0.25, 0.1, 0.75, 0.5])
    assert not mask[10:50, 50:150].any()
    assert mask[60:, :].all() and mask[:, :50].all()
    assert building_set.mask_outside((4, 4), None).all()


def test_gains_for_mean_hits_target_inside_mask_with_clipping():
    pixels = noise(1, 120)
    pixels[:16] = 250  # bright band outside the mask, would clip
    mask = np.ones(pixels.shape[:2], bool)
    mask[:16] = False
    target = np.array([.30, .25, .20])
    gains = building_set.gains_for_mean(pixels, mask, target)
    after = np.minimum(_linear(pixels.astype(float) / 255)[mask] * gains, 1).mean(axis=0)
    assert np.abs(after - target).max() < 1e-6


def spec(textures):
    return {'set': 's', 'base': 'b', 'registry': 'r.jsonl', 'textures': textures}


def test_correct_colours_target_and_match():
    images = {'A.TGA': noise(2, 90), 'B.TGA': noise(3, 140)}
    images['B.TGA'][20:40, 20:40] = 5  # dark window, excluded from the match
    targets = {'A.TGA': np.array([.08, .07, .05]), 'B.TGA': np.array([.5, .5, .5])}
    s = spec({'A.TGA': {'colour': {'mode': 'target'}},
              'B.TGA': {'axes': 'u', 'colour': {'mode': 'match', 'to': 'A.TGA',
                                                'exclude_uv': [20/64, 20/64, 40/64, 40/64]}}})
    corrected, info = building_set.correct_colours(images, s, targets)
    a = _linear(corrected['A.TGA'].astype(float) / 255).mean(axis=(0, 1))
    assert np.abs(a - targets['A.TGA']).max() <= .005
    mask = building_set.mask_outside((64, 64), [20/64, 20/64, 40/64, 40/64])
    b = _linear(corrected['B.TGA'].astype(float) / 255)[mask].mean(axis=0)
    assert np.abs(b - a).max() <= .005
    assert info['B.TGA']['mode'] == 'match' and info['B.TGA']['to'] == 'A.TGA'
    assert info['B.TGA']['masked_fraction'] == pytest.approx(mask.mean())


def test_correct_colours_group_shares_one_gain():
    images = {'A.TGA': noise(4, 100), 'B.TGA': noise(5, 100)}
    targets = {'A.TGA': np.array([.1, .1, .1]), 'B.TGA': np.array([.2, .2, .2])}
    s = spec({n: {'colour': {'mode': 'group', 'group': 'stone', 'area_m2': 10}} for n in images})
    _, info = building_set.correct_colours(images, s, targets)
    assert info['A.TGA']['gains'] == info['B.TGA']['gains']


def test_correct_colours_rejects_match_cycle():
    images = {'A.TGA': noise(6, 100), 'B.TGA': noise(7, 100)}
    targets = {n: np.array([.1, .1, .1]) for n in images}
    s = spec({'A.TGA': {'colour': {'mode': 'match', 'to': 'B.TGA'}},
              'B.TGA': {'colour': {'mode': 'match', 'to': 'A.TGA'}}})
    with pytest.raises(ValueError, match='match cycle'):
        building_set.correct_colours(images, s, targets)


def test_load_spec_validates(tmp_path, monkeypatch):
    monkeypatch.setenv('REMASTER_ASSETS', str(tmp_path))
    folder = tmp_path / 'hut'
    folder.mkdir()
    (folder / 'build.json').write_text(json.dumps(spec({'A.TGA': {'axes': 'w', 'colour': {'mode': 'target'}}})))
    with pytest.raises(ValueError, match='axes'):
        building_set.load_spec('hut')
    (folder / 'build.json').write_text(json.dumps(spec({'A.TGA': {'colour': {'mode': 'match', 'to': 'X.TGA'}}})))
    with pytest.raises(ValueError, match='colour.to'):
        building_set.load_spec('hut')
    (folder / 'build.json').write_text(json.dumps(spec({'A.TGA': {'colour': {'mode': 'target'}}})))
    assert building_set.load_spec('hut')['set'] == 's'
    assert remaster.asset_dir('hut') == folder
