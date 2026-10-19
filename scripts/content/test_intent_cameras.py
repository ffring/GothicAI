"""Camera coordinates and visibility operate on the engine's world conventions."""

import math

import numpy as np

from intent_cameras import Occluders, camera_commands


def test_world_camera_round_trip_preserves_eye_and_target():
    eye = np.array([121.5, 580.2, -732.1])
    hero, target, distance, height, yaw = camera_commands([40.4, 243.1, 54.8], eye)
    angle = math.radians(yaw)
    reconstructed_eye = np.asarray(hero) + [math.cos(angle) * distance, height, math.sin(angle) * distance]
    assert np.allclose(reconstructed_eye, eye)
    assert np.allclose(np.asarray(hero) + [0, 45, 0], target)
    assert np.linalg.norm(np.asarray(target) - [40.4, 243.1, 54.8]) < 1


def test_ray_rejects_occluder_but_allows_target_surface_endpoint():
    wall = [[0, -100, -100], [0, 100, -100], [0, 0, 100]]
    occluders = Occluders([wall])
    assert occluders.first_hit([-50, 0, 0], [50, 0, 0]) == 50
    assert occluders.first_hit([50, 0, 0], [-50, 0, 0]) == 50
    assert occluders.first_hit([-50, 0, 0], [0, 0, 0]) is None
    assert occluders.first_hit([-50, 0, 500], [50, 0, 500]) is None


def test_large_triangle_radius_keeps_far_centroid_in_ray_query():
    # The triangle centroid is far from the short ray, but its surface crosses it.
    occluders = Occluders([[[0, -100000, -100000], [0, 100000, -100000], [0, 0, 100000]]])
    assert occluders.first_hit([-50, 0, 0], [50, 0, 0]) == 50
