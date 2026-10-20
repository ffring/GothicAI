"""Known synthetic periods and orientation exercise the shared decoder core."""
import numpy as np
import pytest

from material_measures import measure_material


def stripes(k_u, k_v, size=128):
    y, x = np.mgrid[:size, :size]
    gray = np.round(128 + 60 * np.cos(2 * np.pi * (k_u*x + k_v*y) / size)).astype(np.uint8)
    return np.repeat(gray[:, :, None], 3, axis=2)


@pytest.mark.parametrize("ku,kv,direction,angle", [(8, 0, "V", 90), (0, 4, "U", 0), (6, 3, "V", 116.565)])
def test_known_stripe_period_and_angle(ku, kv, direction, angle):
    result = measure_material(stripes(ku, kv), uv_m_per_unit=8)
    assert result["period_u_uv"] == pytest.approx(1/ku if ku else 0)
    assert result["period_v_uv"] == pytest.approx(1/kv if kv else 0)
    assert result["period_u_m"] == pytest.approx(8/ku if ku else 0)
    assert result["direction"] == direction
    assert result["direction_angle_deg"] == pytest.approx(angle, abs=1)
    assert result["direction_confidence"] > .5


def test_diagonal_stripes_have_no_claimed_axial_direction():
    result = measure_material(stripes(4, 4))
    assert result["direction"] == "none"
    assert result["direction_confidence"] < .01
    assert result["period_u_uv"] == pytest.approx(.25)
    assert result["direction_angle_deg"] == pytest.approx(135, abs=.1)


def test_solid_fill_has_no_period_or_direction_and_known_linear_color():
    result = measure_material(np.full((64, 64, 3), 128, dtype=np.uint8))
    assert result["period_u_uv"] == result["period_v_uv"] == 0
    assert result["direction"] == "none"
    assert result["direction_confidence"] == 0
    assert result["mean_linear_rgb"] == pytest.approx([.21586050011389926] * 3)
    assert result["oklab"] == pytest.approx([.5998708017, 0, 0], abs=1e-7)


def test_alpha_cutoff_excludes_hidden_color_and_rejects_empty():
    image = np.zeros((16, 16, 4), dtype=np.uint8)
    image[:, :8] = [255, 255, 255, 128]
    image[:, 8:] = [255, 0, 0, 127]
    assert measure_material(image)["mean_linear_rgb"] == pytest.approx([1, 1, 1])
    image[:, :, 3] = 0
    with pytest.raises(ValueError, match="no pixels"):
        measure_material(image)


def test_broad_shading_field_is_not_one_repeating_detail():
    x = np.arange(128, dtype=np.float64)
    gray = np.round(80 + x * .5).astype(np.uint8)
    image = np.repeat(np.tile(gray, (128, 1))[:, :, None], 3, axis=2)
    result = measure_material(image)
    assert result["period_u_uv"] == result["period_v_uv"] == 0


def test_mean_color_averages_linear_rgb_before_oklab():
    image = np.zeros((16, 16, 3), dtype=np.uint8)
    image[:, 8:] = 255
    result = measure_material(image)
    assert result["mean_linear_rgb"] == pytest.approx([.5] * 3)
    assert result["oklab"][0] == pytest.approx(.5 ** (1/3), abs=1e-7)
