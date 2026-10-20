"""One house-wide colour transform and geometry-preserving preparation."""

import numpy as np
from PIL import Image
import pytest

import market_house


TEXTURES = (
    'NW_CITY_WALL_03.TGA',
    'NW_CITY_WINDOW_03.TGA',
    'NW_CITY_FACHWERKBASEMENT_WINDOW_01.TGA',
    'NW_CITY_FACHWERKBASEMENT_EMPTY_01.TGA',
    'NW_CITY_FACHWERKBASEMENT_CHAIN_01.TGA',
    'NW_CITY_WOODBEAM_02.TGA',
)


def linear(pixels):
    srgb = np.asarray(pixels, dtype=float) / 255
    return np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)


def encoded(rgb):
    rgb = np.asarray(rgb, dtype=float)
    srgb = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - .055)
    return np.rint(srgb * 255).astype(np.uint8)


def house_mean(images, areas):
    return sum(areas[name] * linear(pixels).mean(axis=(0, 1))
               for name, pixels in images.items()) / sum(areas.values())


def test_house_colour_matches_area_weighted_linear_target_despite_different_image_sizes():
    rng = np.random.default_rng(192)
    sizes = ((1, 2), (3, 5), (11, 7), (2, 4), (8, 1), (6, 3))
    images = {name: rng.integers(40, 200, (*size, 3), dtype=np.uint8)
              for name, size in zip(TEXTURES, sizes)}
    areas = dict(zip(TEXTURES, (1., 2., 7., 3., 13., 5.)))
    targets = {name: np.array([.12 + i * .008, .14 + i * .006, .10 + i * .01])
               for i, name in enumerate(TEXTURES)}
    expected = sum(areas[name] * targets[name] for name in TEXTURES) / sum(areas.values())
    original = {name: pixels.copy() for name, pixels in images.items()}

    corrected, metadata = market_house.correct_house_colour(images, areas, targets)

    np.testing.assert_allclose(house_mean(corrected, areas), expected, atol=.005, rtol=0)
    np.testing.assert_allclose(metadata['target_linear_rgb'], expected, atol=1e-12, rtol=0)
    np.testing.assert_allclose(metadata['linear_rgb_mean_after'], house_mean(corrected, areas))
    assert metadata['linear_rgb_max_error'] == pytest.approx(
        np.max(np.abs(house_mean(corrected, areas) - expected)))
    assert len(metadata['channel_gains']) == 3
    for name in TEXTURES:
        assert corrected[name].dtype == np.uint8
        assert corrected[name].shape == images[name].shape
        np.testing.assert_array_equal(images[name], original[name])
        np.testing.assert_array_equal(corrected[name],
                                      market_house.apply_gains(images[name], metadata['channel_gains']))


def test_identical_house_materials_receive_identical_pixels_despite_individual_targets():
    pixels = np.array([[[48, 70, 96], [138, 119, 83]],
                       [[190, 174, 153], [80, 92, 104]]], dtype=np.uint8)
    images = {name: pixels.copy() for name in TEXTURES}
    areas = dict(zip(TEXTURES, (1., 3., 8., 2., 6., 4.)))
    targets = {name: np.array([.09 + i * .025, .18 - i * .01, .08 + i * .02])
               for i, name in enumerate(TEXTURES)}

    corrected, metadata = market_house.correct_house_colour(images, areas, targets)

    for name in TEXTURES:
        np.testing.assert_array_equal(corrected[name], corrected[TEXTURES[0]])
        np.testing.assert_array_equal(corrected[name],
                                      market_house.apply_gains(pixels, metadata['channel_gains']))
    expected = sum(areas[name] * targets[name] for name in TEXTURES) / sum(areas.values())
    np.testing.assert_allclose(house_mean(corrected, areas), expected, atol=.005, rtol=0)


def test_house_gain_solver_accounts_for_highlight_clipping():
    # A mean-ratio gain gives red ~= .58 after clipping. The correct gain is
    # about six, retaining a saturated highlight while lifting the dark pixel.
    pixels = encoded([[[.1, .3, .2], [.9, .5, .4]]])
    images = {name: pixels.copy() for name in TEXTURES}
    areas = dict(zip(TEXTURES, (1., 7., 3., 11., 2., 5.)))
    target = np.array([.8, .4, .3])
    targets = {name: target.copy() for name in TEXTURES}

    corrected, metadata = market_house.correct_house_colour(images, areas, targets)

    np.testing.assert_allclose(house_mean(corrected, areas), target, atol=.005, rtol=0)
    assert metadata['channel_gains'][0] > 5
    assert corrected[TEXTURES[0]][0, 1, 0] == 255


@pytest.mark.parametrize('name', TEXTURES)
def test_prepare_resizes_once_with_lanczos_then_seams_only_allowed_axes(name, monkeypatch):
    assert market_house.TEXTURES == TEXTURES
    expected_axes = 'u' if name == 'NW_CITY_WINDOW_03.TGA' else 'uv'
    assert market_house.tiling_axes(name) == expected_axes
    pixels = np.random.default_rng(13).integers(0, 256, (41, 63, 3), dtype=np.uint8)
    source = Image.fromarray(pixels)
    original_resize = Image.Image.resize
    expected = original_resize(source, (1024, 1024), Image.Resampling.LANCZOS)
    calls = []

    def resize(image, size, resample=None, **kwargs):
        calls.append(('resize', size, resample))
        return original_resize(image, size, resample=resample, **kwargs)

    seam_metadata = dict(axes=expected_axes)

    def seamless(image, *, axes, overlap_fraction):
        calls.append(('seamless', image.size, axes, overlap_fraction))
        np.testing.assert_array_equal(image, expected)
        return image, seam_metadata

    monkeypatch.setattr(Image.Image, 'resize', resize)
    monkeypatch.setattr(market_house, 'seamless', seamless)

    result, metadata = market_house.prepare_image(source, name)

    assert calls == [('resize', (1024, 1024), Image.Resampling.LANCZOS),
                     ('seamless', (1024, 1024), expected_axes, 1 / 64)]
    assert result.size == (1024, 1024)
    assert metadata == seam_metadata
    np.testing.assert_array_equal(source, pixels)
