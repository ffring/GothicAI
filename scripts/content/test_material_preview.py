"""Architectural sheet views retain the physical shape and bounded edges."""
from pathlib import Path
import importlib.util

import numpy as np
from PIL import Image


spec = importlib.util.spec_from_file_location("material_preview", Path(__file__).with_name("material_preview.py"))
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def test_tall_beam_preview_restores_physical_proportions():
    # Square storage contains a complete beam, not a square piece of timber.
    square = Image.new("RGB", (16, 16), (200, 100, 60))
    aspect = 1.15 / 10.14
    panel = np.asarray(preview.module_panel(square, 128, aspect))
    material = np.all(panel == (200, 100, 60), axis=2)
    ys, xs = np.nonzero(material)
    assert np.ptp(ys) + 1 == 128
    assert abs((np.ptp(xs) + 1) / 128 - aspect) < 1 / 128


def test_panel_edges_clamp_and_strip_only_wraps_along_its_length():
    image = np.array([[[0.], [.25]], [[.75], [1.]]], dtype=np.float32)
    u, v = np.array([0., 1.]), np.array([1., 0.])
    # A bounded panel retains the distinct top-left and bottom-right corners.
    assert np.allclose(preview.sample_periodic(image, u, v, "")[:, 0], [0., 1.])
    # A vertical strip joins top/bottom while retaining its separate side edges.
    assert np.allclose(preview.sample_periodic(image, u, v, "v")[:, 0], [.375, .625])


def test_oblique_plane_uses_the_beam_shape():
    maps = {"albedo": np.full((8, 8, 3), .7), "normal": np.tile([.5, .5, 1.], (8, 8, 1))}
    maps.update({name: np.full((8, 8, 1), value)
                 for name, value in (("roughness", .8), ("metallic", 0.), ("ao", 1.))})
    square = np.asarray(preview.plane_preview(maps, 128, repeats=(1., 1.), wrap_axes=""))
    beam = np.asarray(preview.plane_preview(maps, 128, aspect=1.15 / 10.14,
                                          repeats=(1., 1.), wrap_axes="v"))
    background = np.rint(np.array([.125, .135, .15]) * 255).astype(np.uint8)
    square_pixels = np.count_nonzero(np.any(square != background, axis=2))
    beam_pixels = np.count_nonzero(np.any(beam != background, axis=2))
    assert 0 < beam_pixels < square_pixels * .2
