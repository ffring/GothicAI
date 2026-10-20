"""Acceptance measurements preserve checker U/V and cylindrical tolerances."""
import json

import numpy as np
import pytest

from synthetic import assert_uv_quality, uv_quality


def cylinder_profile(radii=(.92, .96, 1.04, 1.08, 1.04, .96, .92), sides=128):
    radii = np.asarray(radii)
    heights = np.arange(len(radii), dtype=float) * .3
    angle = np.arange(sides) * 2 * np.pi / sides
    vertices = np.array([[radius * np.cos(theta), height, radius * np.sin(theta)]
                         for height, radius in zip(heights, radii) for theta in angle])
    faces, coords = [], []
    distance = np.r_[0., np.cumsum(np.hypot(np.diff(heights), np.diff(radii)))]
    for ring in range(len(radii) - 1):
        for index in range(sides):
            next_index = (index + 1) % sides
            a, b = ring * sides + index, ring * sides + next_index
            c, d = a + sides, b + sides
            faces.extend([[a, b, c], [b, d, c]])
            ta, tb = index * 2 * np.pi / sides, (index + 1) * 2 * np.pi / sides
            coords.extend([[[ta, distance[ring]], [tb, distance[ring]], [ta, distance[ring + 1]]],
                           [[tb, distance[ring]], [tb, distance[ring + 1]], [ta, distance[ring + 1]]]])
    faces, coords = np.asarray(faces), np.asarray(coords)
    triangles = vertices[faces]
    area = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                   triangles[:, 2] - triangles[:, 0]), axis=1) * .5
    face_radius = np.linalg.norm(triangles[:, :, [0, 2]], axis=2).mean(axis=1)
    coords[:, :, 0] *= np.average(face_radius, weights=area)
    return vertices, faces, coords


def plane():
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [1., 1., 0.]])
    faces = np.array([[0, 1, 2], [1, 3, 2]])
    return vertices, faces, vertices[faces, :2].copy()


def test_bulged_cylinder_accepts_fixed_angle_u_and_metric_meridian_v():
    vertices, faces, uv = cylinder_profile()
    stats = uv_quality(vertices, faces, uv, "cylinder")
    assert_uv_quality(stats)
    assert stats["u_meters_per_uv"]["min"] < .97
    assert stats["u_meters_per_uv"]["max"] > 1.05
    assert stats["v_meters_per_uv"]["min"] == pytest.approx(1, abs=.001)
    assert stats["v_meters_per_uv"]["max"] == pytest.approx(1, abs=.001)
    assert stats["cylindrical"]["u_at_mean_radius_meters_per_uv"] == pytest.approx(1, abs=.001)
    json.dumps(stats, allow_nan=False)


def test_cylinder_rejects_six_percent_v_error():
    vertices, faces, uv = cylinder_profile()
    uv[:, :, 1] /= 1.06
    stats = uv_quality(vertices, faces, uv, "cylinder")
    with pytest.raises(AssertionError, match="V .*outside 1m"):
        assert_uv_quality(stats)


def test_cylinder_rejects_six_percent_u_error_at_mean_radius():
    vertices, faces, uv = cylinder_profile()
    uv[:, :, 0] /= 1.06
    stats = uv_quality(vertices, faces, uv, "cylinder")
    assert stats["u_meters_per_uv"]["max"] < 1.15
    with pytest.raises(AssertionError, match="U at mean radius"):
        assert_uv_quality(stats)


def test_cylinder_rejects_one_sixteen_percent_u_face_without_averaging_it_away():
    vertices, faces, uv = cylinder_profile((1., 1., 1.))
    uv[0, :, 0] /= 1.16
    stats = uv_quality(vertices, faces, uv, "cylinder")
    assert stats["cylindrical"]["u_at_mean_radius_meters_per_uv"] == pytest.approx(1, abs=.001)
    with pytest.raises(AssertionError, match="U .*outside 1m"):
        assert_uv_quality(stats)


@pytest.mark.parametrize("axis", [0, 1])
def test_box_rejects_six_percent_in_either_checker_direction(axis):
    vertices, faces, uv = plane()
    assert_uv_quality(uv_quality(vertices, faces, uv, "box"))
    uv[:, :, axis] /= 1.06
    with pytest.raises(AssertionError, match=f"{'U' if axis == 0 else 'V'} .*outside 1m"):
        assert_uv_quality(uv_quality(vertices, faces, uv, "box"))


def test_directional_vectors_do_not_mix_u_with_v_like_singular_values():
    vertices, faces, uv = plane()
    uv[:, :, 0] -= uv[:, :, 1]
    stats = uv_quality(vertices, faces, uv, "box")
    assert stats["u_meters_per_uv"]["max"] == pytest.approx(1)
    assert stats["v_meters_per_uv"]["min"] == pytest.approx(np.sqrt(2))


@pytest.mark.parametrize("radial_face", [False, True])
def test_cylinder_planar_caps_and_stave_thickness_keep_five_percent_limit(radial_face):
    vertices, faces, uv = plane()
    if radial_face:
        # Vertical radial plane: its normal is the angular tangent.
        vertices[:, 0] += 1
    else:
        # Horizontal cap: the normal is +Y.
        vertices = vertices[:, [0, 2, 1]]
    # Keep the cylinder axis away from this standalone surface's centre.
    vertices = np.vstack((vertices, [-2., -1., -2.], [2., 2., 2.]))
    uv[:, :, 0] /= 1.06
    stats = uv_quality(vertices, faces, uv, "cylinder")
    assert stats["planar"]["triangles"] == 2
    with pytest.raises(AssertionError, match="U .*outside 1m"):
        assert_uv_quality(stats)


def test_degenerate_uv_fails_explicitly_instead_of_being_dropped():
    vertices, faces, uv = plane()
    uv[0] = 0
    with pytest.raises(ValueError, match="nondegenerate UV"):
        uv_quality(vertices, faces, uv, "box")
