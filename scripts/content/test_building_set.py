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


def test_scale_relief_flattens_and_keeps_unit_vectors():
    tilt = np.array([.6, -.3, .74])
    tilt /= np.linalg.norm(tilt)
    encoded = np.tile(tilt * .5 + .5, (4, 4, 1))
    flat = building_set.scale_relief(encoded, 0.)
    assert np.allclose(flat[..., :2], .5) and np.allclose(flat[..., 2], 1.)
    half = building_set.scale_relief(encoded, .5) * 2 - 1
    assert np.allclose(np.linalg.norm(half, axis=-1), 1.)
    assert abs(half[0, 0, 0]) < abs(tilt[0])
    with pytest.raises(ValueError):
        building_set.scale_relief(encoded, 1.5)


def staged(root, name, entries):
    folder = root / 'content-staging' / name / 'materials'
    folder.mkdir(parents=True)
    index = {}
    for texture, stem in entries.items():
        (folder / stem).mkdir()
        for channel in ('albedo', 'normal', 'orm'):
            (folder / stem / f'{channel}.dds').write_bytes(f'{name}/{stem}/{channel}'.encode())
        index[texture] = {k: f'{stem}/{k}.dds' for k in ('albedo', 'normal', 'orm')} | {'scale': 1., 'antitile': 'off'}
    (folder / 'materials.json').write_text(json.dumps(index))


def test_compose_joins_parts_and_rejects_shared_texture(tmp_path, monkeypatch):
    monkeypatch.setenv('GOTHIC_AI_DATA', str(tmp_path))
    staged(tmp_path, 'roofs-1', {'ROOF_01.TGA': 'roofs-1-roof-01'})
    staged(tmp_path, 'hut-4', {'WAND_01.TGA': 'hut-4-wand-01', 'WAND_02.TGA': 'hut-4-wand-02'})
    result = building_set.compose('hut-4-review', ['roofs-1', 'hut-4'])
    assert result == {'set': 'hut-4-review', 'entries': 3, 'parts': {'roofs-1': 1, 'hut-4': 2}}
    out = tmp_path / 'content-staging/hut-4-review'
    index = json.loads((out / 'materials/materials.json').read_text())
    assert sorted(index) == ['ROOF_01.TGA', 'WAND_01.TGA', 'WAND_02.TGA']
    assert (out / 'materials/hut-4-wand-02/orm.dds').read_bytes() == b'hut-4/hut-4-wand-02/orm'
    assert json.loads((out / 'composition.json').read_text())['parts']['roofs-1'] == ['ROOF_01.TGA']
    staged(tmp_path, 'house-3', {'ROOF_01.TGA': 'house-3-roof-01'})
    with pytest.raises(ValueError, match='in both roofs-1 and house-3'):
        building_set.compose('mixed', ['roofs-1', 'house-3'])
    with pytest.raises(ValueError, match='staging set exists'):
        building_set.compose('hut-4-review', ['roofs-1', 'hut-4'])


def test_crop_band_drops_grey_margins():
    from PIL import Image
    canvas = Image.new('RGB', (300, 200), (200, 200, 200))
    canvas.paste(Image.fromarray(noise(9, 60, size=100).repeat(2, axis=1)), (50, 40))
    band, box = building_set.crop_band(canvas)
    assert box == [50, 40, 250, 140] and band.size == (200, 100)


def test_model_maps_line_up_with_the_albedo(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setenv('GOTHIC_AI_DATA', str(tmp_path))
    folder = tmp_path / 'content-sources' / 'roofs'
    folder.mkdir(parents=True)
    normal = np.zeros((512, 512, 3), np.uint8) + np.array([200, 128, 200], np.uint8)
    Image.fromarray(normal).save(folder / 'S-attempt-1.normal.png')
    Image.fromarray(np.full((512, 512), 77, np.uint8)).save(folder / 'S-attempt-1.roughness.png')
    maps = {k: dict(file=f'roofs/S-attempt-1.{k}.png', sha256=remaster.sha256(folder / f'S-attempt-1.{k}.png'))
            for k in ('normal', 'roughness')}
    source = dict(file='roofs/S-attempt-1.png', maps=maps)
    with pytest.raises(ValueError, match='axes'):
        building_set.load_model_maps(source, {'axes': 'uv'})
    with pytest.raises(ValueError, match='needs normal and roughness'):
        building_set.load_model_maps(dict(source, maps={'normal': maps['normal']}), {'axes': ''})
    loaded = building_set.load_model_maps(source, {'axes': ''})
    assert loaded['normal'].shape == (1024, 1024, 3) and loaded['roughness'].shape == (1024, 1024)
    vector = loaded['normal'] * 2 - 1
    np.testing.assert_allclose(np.linalg.norm(vector, axis=-1), 1, atol=1e-6)
    assert abs(loaded['roughness'].mean() - 77 / 255) < 1e-3
    Image.fromarray(np.full((512, 512), 78, np.uint8)).save(folder / 'S-attempt-1.roughness.png')
    with pytest.raises(ValueError, match='SHA-256'):
        building_set.load_model_maps(source, {'axes': ''})
