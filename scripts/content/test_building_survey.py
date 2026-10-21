"""Geometry-only building census, exclusions, and ground-level camera framing."""

import csv

import numpy as np
import pytest

import building_survey as survey


def triangle(points, texture='WALL.TGA'):
    points = np.asarray(points, dtype=float)
    cross = np.cross(points[1] - points[0], points[2] - points[0])
    return dict(points=points.tolist(), texture=texture, visual='',
                center=points.mean(axis=0).tolist(),
                normal=(cross / np.linalg.norm(cross)).tolist(),
                area_m2=float(np.linalg.norm(cross) / 20000))


def quad(a, b, c, d, texture='WALL.TGA'):
    return [triangle((a, b, c), texture), triangle((a, c, d), texture)]


def house(x=0, front='WALL.TGA'):
    # A 4 x 4 x 5 m shell, with no floor. Adjacent x positions share a wall.
    return (quad((x, 0, 0), (x + 400, 0, 0), (x + 400, 400, 0), (x, 400, 0), front)
            + quad((x, 0, 500), (x, 400, 500), (x + 400, 400, 500), (x + 400, 0, 500))
            + quad((x, 0, 0), (x, 400, 0), (x, 400, 500), (x, 0, 500))
            + quad((x + 400, 0, 0), (x + 400, 0, 500), (x + 400, 400, 500), (x + 400, 400, 0))
            + quad((x, 400, 0), (x + 400, 400, 0), (x + 400, 400, 500), (x, 400, 500)))


def components(triangles, **kwargs):
    return {frozenset(group) for group in survey.cluster_triangles(triangles, **kwargs)}


def test_two_houses_with_shared_wall_are_one_connected_shell():
    # This census counts connected structures: a party wall joins both houses.
    triangles = house() + house(400)
    assert components(triangles) == {frozenset(range(20))}


@pytest.mark.parametrize('gap_cm, expected', [(50, 1), (50.01, 2), (175, 2)])
def test_face_gap_threshold_between_separate_houses(gap_cm, expected):
    triangles = house() + house(400 + gap_cm)
    groups = components(triangles)
    assert len(groups) == expected
    assert set().union(*groups) == set(range(20))
    if expected == 2:
        assert groups == {frozenset(range(10)), frozenset(range(10, 20))}


def test_centimeters_round_shared_vertices_before_face_proximity():
    first = triangle(((0, 0, 0), (100, 0, 0), (0, 100, 0)))
    second = triangle(((0, 0, .49), (100, 0, .49), (0, 100, .49)))
    assert components([first, second], gap_cm=0) == {frozenset((0, 1))}


@pytest.mark.parametrize('gap_cm, expected', [(0, 1), (49.99, 1), (50, 1), (50.01, 2)])
def test_mid_edge_face_proximity_does_not_depend_on_vertices_or_centroids(gap_cm, expected):
    # Crossed long edges are closest in their middles. Every vertex pair and
    # both centroids remain farther apart than the allowed 50 cm.
    a = triangle(((-1000, 0, 0), (1000, 0, 0), (0, -100, 0)))
    b = triangle(((0, -1000, gap_cm), (0, 1000, gap_cm), (200, 0, gap_cm)))
    pa, pb = np.asarray(a['points']), np.asarray(b['points'])
    assert np.linalg.norm(pa[:, None] - pb[None, :], axis=2).min() > 50
    assert np.linalg.norm(pa.mean(axis=0) - pb.mean(axis=0)) > 50
    assert survey.face_distance(pa[None], pb[None])[0] == pytest.approx(gap_cm)
    assert len(components([a, b])) == expected


def test_edge_piercing_triangle_interior_has_zero_distance():
    floor = triangle(((-1000, 0, -1000), (1000, 0, -1000), (0, 0, 1000)))
    crossing = triangle(((-100, -100, 0), (100, -100, 0), (0, 100, 0)))
    # No vertex lies on either triangle, and the small triangle pierces the
    # large face away from all three large boundary edges.
    assert survey.face_distance([floor['points']], [crossing['points']])[0] == 0
    assert components([floor, crossing], gap_cm=0) == {frozenset((0, 1))}


def test_shell_selection_keeps_architecture_and_rejects_non_shell_roles():
    with (survey.ROOT / 'data/content/city-materials.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    selected = survey.shell_rows(rows)
    assert {
        'NW_CITY_WALL_03.TGA', 'NW_CITY_WINDOW_03.TGA',
        'NW_CITY_WOODBEAM_02.TGA', 'NW_CITY_FACHWERKBASEMENT_EMPTY_01.TGA',
        'NW_CITY_HAFEN_GIEBEL_01.TGA', 'NW_CITY_HAFEN_HOLZWAND_01.TGA',
        'NW_CITY_HAFEN_HOLZWAND_02.TGA', 'NW_CITY_ROOF_01.TGA',
        'NW_CITY_ROOF_04.TGA', 'NW_CITY_STONEWALL_02.TGA',
        'NW_MISC_STRONG_STONEWALL_01.TGA',
    } <= selected.keys()
    assert not ({
        'NW_CITY_DIRTYGROUND_02.TGA', 'NW_CITY_DIRTYGROUND_WEG_02.TGA',
        'NW_CITY_BODENDIELEN_01.TGA', 'NW_CITY_INSIDEFLOOR_11.TGA',
        'NW_CITY_DUNGEONFLOOR_02.TGA', 'NW_CITY_PFLASTERSTEIN_01.TGA',
        'NW_CITY_HAFENKAI_BODEN_01.TGA', 'NW_CITY_HAFENKAI_BODEN_TRANS_01.TGA',
        'NW_CITY_INSIDEWALL_11.TGA', 'NW_CITY_INSIDEWALL_12.TGA',
        'NW_CITY_INSIDEWALL_13.TGA', 'NW_CITY_BARREL_01.TGA',
        'NW_HARBOUR_WOODCRATE_01.TGA', 'NW_HARBOUR_CANON_01.TGA',
        'NW_HARBOUR_FISHINGNET_01.TGA', 'NW_CITY_ROPE_01.TGA',
        'NW_HARBOUR_ROPEWALL_01.TGA', 'NW_CITY_IVY_FULL_01.TGA',
        'NW_CITY_GITTERZAUN_01.TGA', 'NW_HARBOUR_WOODENGATE_01.TGA',
    } & selected.keys())


def test_exclusion_decision_overrides_an_architectural_class():
    rows = [dict(texture='WALL.TGA', decision='include', **{'class': 'planks_wall'}),
            dict(texture='ALPHA.TGA', decision='exclude', **{'class': 'planks_wall'}),
            dict(texture='NW_CITY_INSIDEWALL_TEST.TGA', decision='module',
                 **{'class': 'framed_masonry_tile'})]
    assert set(survey.shell_rows(rows)) == {'WALL.TGA'}


def test_props_reusing_shell_materials_do_not_enter_building_census():
    texture = 'NW_CITY_WOODBEAM_02.TGA'
    rows = survey.shell_rows([
        dict(texture=texture, decision='module', **{'class': 'bound_timber_tile'})])
    surface = triangle(((0, 0, 0), (100, 0, 0), (0, 100, 0)), texture)
    building_visuals = ('', 'NW_CITY_BEAM_01.MRM', 'NW_MISC_POOR_HUT_01.MRM')
    prop_visuals = ('NW_HARBOUR_CANON_01.MRM', 'NW_HARBOUR_FISHINGNET_01.MRM',
                    'NW_CITY_SHELF_01.MRM', 'NW_CITY_SIGN_01.MRM',
                    'NW_HARBOUR_SHIP_01.MRM', 'LOOSE_BOARDS.MRM')
    selected = {visual for visual in building_visuals + prop_visuals
                if survey.shell_triangle(dict(surface, visual=visual), rows)}
    assert selected == set(building_visuals)
    assert not survey.shell_triangle(dict(surface, area_m2=0), rows)
    assert not survey.shell_triangle(dict(surface, texture='GROUND.TGA'), rows)


def test_ground_does_not_weld_disconnected_house_shells_after_filtering():
    triangles = house() + house(800)
    # One ground triangle touches a vertex on each house and would bridge them.
    triangles += [triangle(((0, 0, 0), (1200, 0, 0), (0, 0, 1000)), 'GROUND.TGA')]
    assert len(components(triangles)) == 1
    rows = [dict(texture='WALL.TGA', decision='include', **{'class': 'masonry_rubble'}),
            dict(texture='GROUND.TGA', decision='include', **{'class': 'mud'})]
    selected = survey.shell_rows(rows)
    shell = [t for t in triangles if t['texture'] in selected]
    assert len(shell) == 20
    assert components(shell) == {frozenset(range(10)), frozenset(range(10, 20))}


def test_camera_is_outside_largest_shutter_wall_at_ground_plus_170_and_frames_shell():
    triangles = house(front=survey.HARBOUR[1])
    # The archive stores oriented exterior normals for this front wall.
    for t in triangles[:2]:
        t['normal'] = [0, 0, -1]
    cluster = survey.describe(triangles, list(range(len(triangles))),
                              [dict(name='ENTRY', position=[0, 0, 0])])
    cluster['id'] = 'B001'
    ground = [t['points'] for t in quad((-100000, 25, -100000), (100000, 25, -100000),
                                       (100000, 25, 100000), (-100000, 25, 100000))]
    result = survey.camera(cluster, triangles, ground)
    eye, target = np.asarray(result['eye_cm']), np.asarray(result['target_cm'])
    assert result['ground_y_cm'] == 25
    assert eye[1] == 195
    assert eye[2] < 0
    np.testing.assert_allclose(result['outward'], [0, 0, -1])
    np.testing.assert_allclose(target, [200, 200, 0])
    assert result['face_area_m2'] == pytest.approx(16)
    points = np.asarray([t['points'] for t in triangles]).reshape(-1, 3)
    xy, depth = survey.project(points, eye, target)
    assert depth.min() > 0
    assert np.max(np.abs(xy)) <= .9


def test_camera_chooses_largest_connected_wall_not_largest_single_triangle():
    texture = survey.HARBOUR[1]
    front = [t for x in range(0, 600, 150)
             for t in quad((x, 0, 0), (x, 400, 0), (x + 150, 400, 0),
                           (x + 150, 0, 0), texture)]
    side = quad((600, 0, 0), (600, 400, 0), (600, 400, 400),
                (600, 0, 400), texture)
    assert max(t['area_m2'] for t in front) < min(t['area_m2'] for t in side)
    triangles = front + side
    cluster = survey.describe(triangles, list(range(len(triangles))),
                              [dict(name='ENTRY', position=[0, 0, 0])])
    cluster['id'] = 'B001'
    ground = [t['points'] for t in quad((-100000, 0, -100000), (100000, 0, -100000),
                                       (100000, 0, 100000), (-100000, 0, 100000))]
    result = survey.camera(cluster, triangles, ground)
    assert result['face_area_m2'] == pytest.approx(24)
    np.testing.assert_allclose(result['outward'], [0, 0, -1])


def test_ground_height_uses_highest_surface_below_camera_limit():
    grounds = [t['points'] for y in (-20, 40, 900)
               for t in quad((-100, y, -100), (100, y, -100),
                             (100, y, 100), (-100, y, 100))]
    assert survey.ground_height(grounds, 10, 20, below=200) == 40
    with pytest.raises(ValueError, match='No ground below camera'):
        survey.ground_height(grounds, 500, 20, below=200)
