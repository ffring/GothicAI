import numpy as np
import pytest

import timber_bake as tb


@pytest.fixture(scope='module')
def built():
    return tb.beam02(seed=0)


def at(grid, step, u, v, period=tb.PERIOD):
    """Height of a grid at texture u (from the left) and v (from the top)."""
    row = int(round((1 - v % 1) * period[1] / step[1])) % (grid.shape[0] - 1)
    return grid[row, int(round(u * period[0] / step[0]))]


def test_layout_is_reproducible_and_wraps(built):
    spec, _, grids = built
    again = tb.beam02(seed=0)[2]
    assert all(np.array_equal(grids[k], again[k]) for k in grids)
    face = grids[0]
    assert np.allclose(face[0], face[-1])                     # the last row closes the period
    assert [p['kind'] for p in spec['pieces']] == ['face', 'strap', 'nail', 'nail']


def test_cracks_where_the_original_has_them(built):
    spec, _, grids = built
    face = grids[0]
    for crack in tb.CRACKS[:2]:
        middle = crack['path'][len(crack['path']) // 2]
        v, u = middle
        window = [at(face, tb.STEP, u + du, v) for du in np.linspace(-.02, .02, 21)]
        assert min(window) < -.6 * crack['depth']             # deep at its measured place
    # away from every crack the face is only hewn: scallops, grain, pockets and the worn arrises
    for u, v in ((.85, .5), (.15, .6), (.75, .35)):
        assert at(face, tb.STEP, u, v) > -.012


def test_strap_and_nails_at_the_measured_places(built):
    spec, points, grids = built
    strap = spec['pieces'][1]
    top, bottom = (1 - tb.STRAP[0]) * tb.PERIOD[1], (1 - tb.STRAP[1]) * tb.PERIOD[1]
    y0, dy = strap['grid'][1], strap['grid'][3]
    ys = y0 + dy * np.arange(grids[1].shape[0])
    middle = grids[1][np.argmin(abs(ys - (top + bottom) / 2))]
    face_rows = grids[0][np.round(ys / tb.STEP[1]).astype(int) % (grids[0].shape[0] - 1)]
    assert np.all(middle > face_rows[np.argmin(abs(ys - (top + bottom) / 2))])       # on top of the face
    nails = [np.mean([q[0] for q in p['outline']]) / tb.PERIOD[0] for p in spec['pieces'] if p['kind'] == 'nail']
    assert np.allclose(sorted(nails), tb.NAILS[0], atol=.01)


def test_compose_blackens_cracks_and_keeps_the_strap_iron():
    spec = dict(period=[.5, 1.], strap=dict(v=list(tb.STRAP[:2])),
                pieces=[dict(id=0, kind='face'), dict(id=1, kind='strap')])
    rows, cols = 128, 64
    piece = np.zeros((rows, cols), int)
    piece[20:26] = 1                                                    # the strap across, near the top
    crevice = np.ones((rows, cols))
    crevice[:, 30:33] = .1                                              # a crack down the face
    y = np.repeat(np.linspace(.99, .01, rows)[:, None], cols, axis=1)
    passes = dict(piece=piece, y=y, x=np.tile(np.linspace(0, .5, cols), (rows, 1)),
                  colour=np.full((rows, cols, 3), .2), sky=np.ones((rows, cols)), crevice=crevice,
                  ao=np.ones((rows, cols)), height=np.zeros((rows, cols)),
                  normal=np.dstack([np.zeros((rows, cols))] * 2 + [np.ones((rows, cols))]))
    result = tb.compose(passes, spec, seed=1)
    lum = result['albedo'] @ tb.LUM
    face = piece == 0
    crack = face & (crevice < .5)
    assert lum[crack].mean() < .1 * lum[face & ~crack].mean()
    assert result['roughness'][piece == 1].max() == pytest.approx(tb.ROUGH[2])
    assert result['roughness'][face & ~crack].min() >= tb.ROUGH[1] - 1e-9


@pytest.fixture(scope='module')
def woods():
    return {name: tb.wood(name, seed=0) for name in tb.WOODS}


def test_every_wood_builds_and_wraps(woods):
    for name, (spec, _, grids) in woods.items():
        assert spec['texture'] == tb.WOODS[name]['texture']
        for piece in spec['pieces']:
            grid = grids.get(piece['id'])
            if piece['kind'] == 'face':
                assert np.allclose(grid[0], grid[-1])                 # closes along the length
            elif piece['kind'] == 'board':
                assert np.allclose(grid[:, 0], grid[:, -1])           # boards close along x


def test_carving_at_the_measured_places(woods):
    spec, _, grids = woods['carved']
    cfg = tb.WOODS['carved']
    face, step, carving = grids[0], cfg['step'], cfg['carving']
    for v in carving['rings']:
        assert at(face, step, carving['u'], v, cfg['period']) < -.8 * carving['depth']    # the cut-away disc
    assert at(face, step, carving['u'], carving['square'][0], cfg['period']) < -.8 * carving['depth']
    for v in (.35, .55):                                     # the bands between are left standing
        assert at(face, step, .9, v, cfg['period']) > -.006


def test_three_boards_with_gaps_at_the_original_joints(woods):
    spec, _, grids = woods['floor']
    boards = [p for p in spec['pieces'] if p['kind'] == 'board']
    assert len(boards) == 3 and spec['void']['z'] < -.01
    py = tb.WOODS['floor']['period'][1]
    spans = sorted((p['grid'][1], p['grid'][1] + p['grid'][3] * (grids[p['id']].shape[0] - 1)) for p in boards)
    for (_, top), (bottom, _), joint in zip(spans, spans[1:], (.67, .32)):
        assert top < (1 - joint) * py < bottom                     # the gap sits on the joint


def test_ship_beam_strap_low_and_no_long_cracks(woods):
    spec, _, _ = woods['beam03']
    assert spec['strap']['v'] == [.72, .76] and len([p for p in spec['pieces'] if p['kind'] == 'nail']) == 2
    assert all(abs(c['path'][-1][0] - c['path'][0][0]) < .2 for c in spec['cracks'])
