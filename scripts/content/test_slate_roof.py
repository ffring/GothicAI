import numpy as np
from PIL import Image

import slate_roof


def surface(seed):
    rng = np.random.default_rng(seed)
    return Image.fromarray(np.clip(rng.normal(60, 12, (400, 400, 3)), 0, 255).astype(np.uint8))


def seam_ratio(image, axis, inner):
    """Jump across the wrap edge against the jump across interior pixel pairs (i, i + 1)."""
    pixels = image.astype(float)
    wrap = np.abs(np.take(pixels, 0, axis) - np.take(pixels, -1, axis)).mean()
    return wrap / np.mean([np.abs(np.take(pixels, i, axis) - np.take(pixels, i + 1, axis)).mean() for i in inner])


def test_slate_square_tiles_and_varies():
    image = slate_roof.assemble([surface(1), surface(2)], seed=3)
    assert image.shape == (1024, 1024, 3)
    assert seam_ratio(image, 1, range(100, 110)) < 1.5
    # the vertical wrap is a course boundary: compare with interior course boundaries
    height = 1024 / slate_roof.COURSES
    assert seam_ratio(image, 0, [int(np.ceil(k * height)) - 1 for k in range(3, 9)]) < 1.5
    rng = np.random.default_rng(3)
    course, slate, u, v, width, height = slate_roof.layout(rng)
    assert len(np.unique(course * slate_roof.ACROSS + slate)) == slate_roof.ACROSS * slate_roof.COURSES
    # joints slant from upper left to lower right: a slate's left edge moves right down the course
    rows = [int(height * f) for f in (.1, .9)]
    left_edges = [np.argmax(slate[r] != slate[r, 0]) for r in rows]
    assert left_edges[1] != left_edges[0]
    assert (slate_roof.assemble([surface(1), surface(2)], seed=3) == image).all()
