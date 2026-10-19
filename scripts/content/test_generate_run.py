"""CPU regressions for numerical admission at the end of a FLUX retry series."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import generate
import pack_materials


def material():
    return dict(id='city-1-ground', replaces=['GROUND.TGA'], seed=1, files={},
        parameters=dict(set='city-1', material='city-1-ground',
            luminance_range=[.06, .14], saturation_limit=.3,
            roughness_range=[.8, .95], roughness_base=.875),
        generation=dict(outcome='pending', attempts=[], attempt_limit=3))


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    """Replace only GPU/model plumbing; filtering and PNG/registry writes are real."""
    monkeypatch.setattr(generate.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(generate.Path, 'home', classmethod(lambda cls: tmp_path))
    root = tmp_path/'gothic-ai/materials'
    images, calls, loads = [], [], []

    class FakeGenerator:
        def __init__(self, device):
            assert device == 'cpu'

        def manual_seed(self, seed):
            return self

    class Embedding:
        def to(self, *args, **kwargs):
            return self

    class Pipeline:
        transformer = SimpleNamespace(dtype='fake', _tile_step=0)

        def __call__(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(images=[images.pop(0)])

    def load_flux(records, root, pending):
        loads.append(len(pending))
        return Pipeline(), {r['parameters']['material']: (Embedding(), Embedding()) for r in pending}, {
            r['parameters']['material']: {'fake_cpu_test': True} for r in pending}

    torch = SimpleNamespace(set_num_threads=lambda n: None,
        backends=SimpleNamespace(cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=False))),
        cuda=SimpleNamespace(reset_peak_memory_stats=lambda: None, max_memory_allocated=lambda: 0),
        Generator=FakeGenerator)
    monkeypatch.setitem(sys.modules, 'torch', torch)
    monkeypatch.setattr(generate.bakeoff, 'gpu_state', lambda *args: None)
    monkeypatch.setattr(generate.bakeoff, 'load_flux', load_flux)
    return SimpleNamespace(root=root, registry=tmp_path/'registry.jsonl', images=images,
        calls=calls, loads=loads)


def candidate(runtime, record, colour, number, visual_reason='large isolated patches'):
    image = Image.new('RGB', (32, 32), colour)
    maps, correction, rejection = generate.corrected_maps(image, record)
    return dict(number=number, seed=number, outcome='rejected',
        reason=rejection or visual_reason, brightness_correction=correction,
        files=generate.save_attempt(runtime.root, record, maps, number))


def test_three_real_black_attempts_fail_and_retry_does_not_reload_flux(runtime):
    record = material()
    runtime.images.extend([Image.new('RGB', (32, 32), 'black') for _ in range(3)])
    for retry in range(2):
        with pytest.raises(RuntimeError, match='3 attempts.*no numerically valid candidate.*brightness'):
            generate.run([record], runtime.registry, runtime.root, 'city-1')
        assert record['generation']['outcome'] == 'pending'
        assert record['files'] == {}
        assert len(record['generation']['attempts']) == 3
        assert len(runtime.calls) == 3 and runtime.loads == [1]
    saved = generate.bakeoff.read_registry(runtime.registry)[0]
    assert saved == record
    mapping = runtime.registry.with_name('mapping.csv')
    mapping.write_text('texture,class,scale,decision\nGROUND.TGA,mud,1,include\n')
    with pytest.raises(ValueError, match='missing completed materials for GROUND.TGA'):
        pack_materials.select_set_records([saved], 'city-1', mapping)
    for attempt in saved['generation']['attempts']:
        assert attempt['outcome'] == 'rejected' and not attempt['numeric_valid']
        assert attempt['numeric_rejection'] == 'brightness correction could not reach class range'
        assert attempt['brightness_correction']['linear_mean_after'] == 0
        assert all((runtime.root/'out'/path).is_file() for path in attempt['files'].values())
        assert (runtime.root/'out'/Path(attempt['files']['albedo']).parent/'source.png').is_file()


def test_exhausted_mixed_series_selects_only_numerically_valid_visual_reject(runtime):
    record = material()
    attempts = [candidate(runtime, record, 'black', 1),
        candidate(runtime, record, (90, 90, 90), 2), candidate(runtime, record, 'black', 3)]
    # Numerical metadata is checked again; an old or stale boolean is no proof.
    for attempt in attempts:
        attempt.update(numeric_valid=True, numeric_rejection=None)
    record['generation']['attempts'] = attempts
    generate.run([record], runtime.registry, runtime.root, 'city-1')
    assert record['generation']['outcome'] == 'complete'
    assert record['generation']['selected_attempt'] == 2
    assert record['files'] == attempts[1]['files']
    assert [a['numeric_valid'] for a in attempts] == [False, True, False]
    assert attempts[1]['outcome'] == 'rejected'  # Preserve its visual review.
    assert 'best numerically valid' in record['generation']['selection_note']
    assert runtime.loads == [] and runtime.calls == []


def test_all_visual_rejects_can_retain_best_available_without_loading_flux(runtime):
    record = material()
    attempts = [candidate(runtime, record, (v, v, v), n) for n, v in enumerate((75, 85, 99), 1)]
    record['generation']['attempts'] = attempts
    generate.run([record], runtime.registry, runtime.root, 'city-1')
    assert record['generation']['outcome'] == 'complete'
    assert all(a['numeric_valid'] for a in attempts)
    selected = attempts[record['generation']['selected_attempt']-1]
    assert record['files'] == selected['files']
    assert selected['reason'] == 'large isolated patches'
    assert runtime.loads == [] and runtime.calls == []


def test_final_quantized_luminance_after_saturation_is_the_admission_measurement():
    record = material()
    image = Image.new('RGB', (32, 32), (150, 65, 30))
    rgb = np.asarray(image, dtype=np.float64)/255
    linear = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
    before = float((linear @ np.array([.2126, .7152, .0722])).mean())
    record['parameters']['luminance_range'] = [before-1e-8, before+1e-8]
    maps, correction, rejection = generate.corrected_maps(image, record)
    rgb = np.rint(maps['albedo']*255).astype(np.float64)/255
    linear = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
    stored_y = float((linear @ np.array([.2126, .7152, .0722])).mean())
    assert correction['linear_mean_after'] == pytest.approx(stored_y, abs=1e-12)
    assert abs(stored_y-before) > 1e-8
    assert rejection == 'brightness correction could not reach class range'


@pytest.mark.parametrize('change', ['saturation', 'clipping'])
def test_select_attempt_cannot_bypass_numerical_constraints(runtime, change):
    record = material()
    attempt = candidate(runtime, record, (90, 90, 90), 1)
    if change == 'saturation':
        attempt['brightness_correction']['saturation']['mean_hsv_s_after'] = .31
    else:
        attempt['brightness_correction']['clipped_channel_fraction'] = .41
    snapshot = deepcopy(record)
    with pytest.raises(ValueError, match='cannot select numerically invalid attempt'):
        generate.select_attempt(record, attempt)
    assert record == snapshot
