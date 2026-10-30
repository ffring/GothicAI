"""Tests of the world edit tool (world_edit.py, chunk_glb.py, world_bpy.py).

The check runs on synthetic chunks without Blender. The Blender tests build a synthetic world of two chunks and
apply the test edits of test_edits/; they skip without Blender. The rebuild tests compare the records of
data/content/world-edits.jsonl with the chunks published on this machine; they skip without the base or the sets.
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chunk_glb  # noqa: E402
import world_edit  # noqa: E402

EDITS = HERE / 'test_edits'
MATERIALS = [{'name': 'TEST_A', 'texture': 'TEST_A.TGA'}, {'name': 'TEST_B', 'texture': 'TEST_B.TGA'}]


def write_glb(path, primitives, materials=MATERIALS):
    """A chunk as the converter writes it: per primitive POSITION, NORMAL, TEXCOORD_0, _VERTEX, indices."""
    binary = bytearray()
    views, accessors, prims = [], [], []

    def add(array, kind, component, target=None, minmax=False):
        array = np.ascontiguousarray(array)
        while len(binary) % 4:
            binary.append(0)
        view = {'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes}
        if target:
            view['target'] = target
        binary.extend(array.tobytes())
        views.append(view)
        acc = {'bufferView': len(views) - 1, 'componentType': component, 'count': len(array), 'type': kind}
        if minmax:
            acc['min'], acc['max'] = array.min(0).tolist(), array.max(0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    for material, positions, ids, triangles in primitives:
        pos = np.array(positions, np.float32)
        n = len(pos)
        attrs = {'POSITION': add(pos, 'VEC3', 5126, 34962, True),
                 'NORMAL': add(np.tile(np.float32([0, 1, 0]), (n, 1)), 'VEC3', 5126, 34962),
                 'TEXCOORD_0': add(pos[:, [0, 2]] / 100, 'VEC2', 5126, 34962),
                 '_VERTEX': add(np.array(ids, np.float32), 'SCALAR', 5126, 34962)}
        prims.append({'attributes': attrs, 'indices': add(np.array(triangles, np.uint32).reshape(-1), 'SCALAR', 5125, 34963),
                      'material': material, 'mode': 4})
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
           'nodes': [{'name': path.stem, 'mesh': 0, 'scale': [.01, .01, .01]}],
           'meshes': [{'name': path.stem, 'primitives': prims}],
           'materials': [{'name': m['name'], 'extras': {'gothic': {'index': i, **m}}} for i, m in enumerate(materials)],
           'accessors': accessors, 'bufferViews': views, 'buffers': [{'byteLength': len(binary)}]}
    text = json.dumps(doc).encode()
    text += b' ' * (-len(text) % 4)
    binary += b'\0' * (-len(binary) % 4)
    out = struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(text) + 8 + len(binary))
    out += struct.pack('<II', len(text), 0x4E4F534A) + text + struct.pack('<II', len(binary), 0x004E4942) + bytes(binary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


def grid(i0, i1, j0=0, j1=2):
    """Quads over glTF x = 100 i, z = 100 j; vertex id 3 i + j; each quad two triangles."""
    verts = [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]
    index = {v: k for k, v in enumerate(verts)}
    tris = []
    for i in range(i0, i1):
        for j in range(j0, j1):
            a, b, c, d = index[(i, j)], index[(i + 1, j)], index[(i + 1, j + 1)], index[(i, j + 1)]
            tris += [(a, c, b), (a, d, c)]
    return [(100.0 * i, 0.0, 100.0 * j) for i, j in verts], [3 * i + j for i, j in verts], tris


def world(tmp_path):
    """A base with two chunks side by side: 0_0 is the grid i 0..2, 1_0 the grid i 2..3 (shares i = 2)."""
    base = tmp_path / 'base'
    chunks = base / 'worlds/TESTWORLD/chunks'
    pos, ids, tris = grid(0, 2)
    write_glb(chunks / '0_0.glb', [(0, pos, ids, tris)])
    pos, ids, tris = grid(2, 3)
    write_glb(chunks / '1_0.glb', [(1, pos, ids, tris)])
    (base / 'worlds/TESTWORLD/world.json').write_text(json.dumps({'materials': MATERIALS}))
    return base


def edited(tmp_path, base, change):
    pos, ids, tris = grid(0, 2)
    pos, ids, tris, prims = change(list(pos), list(ids), list(tris))
    out = tmp_path / 'edited.glb'
    write_glb(out, prims or [(0, pos, ids, tris)],
              MATERIALS + [{'name': 'TEST_UNKNOWN', 'texture': 'TEST_UNKNOWN.TGA'}])
    return chunk_glb.check(base / 'worlds/TESTWORLD/chunks/0_0.glb', out)


def test_check_passes_an_unchanged_chunk(tmp_path):
    base = world(tmp_path)
    report = edited(tmp_path, base, lambda p, i, t: (p, i, t, None))
    assert report.ok, report.problems
    assert report.summary['border_vertices'] == 3
    assert report.summary['triangles'] == [8, 8]


def test_check_catches_a_moved_border_vertex(tmp_path):
    base = world(tmp_path)

    def move(p, i, t):
        p[i.index(7)] = (200.0, 10.0, 100.0)
        return p, i, t, None
    report = edited(tmp_path, base, move)
    assert any('border vertex 7 moved' in s for s in report.problems)
    assert any('border vertex 7' in s and 'gone' in s for s in report.problems)


def test_check_catches_a_new_open_edge(tmp_path):
    base = world(tmp_path)
    report = edited(tmp_path, base, lambda p, i, t: (p, i, t[1:], None))
    assert any(s.startswith('new open edge') for s in report.problems)
    assert report.summary['materials']['TEST_A']['triangles'] == [8, 7]


def test_check_catches_an_unknown_material(tmp_path):
    base = world(tmp_path)
    report = edited(tmp_path, base, lambda p, i, t: (p, i, t, [(0, p, i, t[1:]), (2, p, i, t[:1])]))
    assert any("unknown material 'TEST_UNKNOWN'" in s for s in report.problems)
    index = tmp_path / 'materials.json'
    index.write_text(json.dumps({'TEST_UNKNOWN.TGA': {}}))
    out = tmp_path / 'edited.glb'
    known = chunk_glb.check(base / 'worlds/TESTWORLD/chunks/0_0.glb', out, material_indexes=[index])
    assert known.ok, known.problems


def test_check_reports_area_per_material(tmp_path):
    base = world(tmp_path)

    def lift(p, i, t):
        p[i.index(4)] = (100.0, 100.0, 100.0)    # the middle vertex: not on the border
        return p, i, t, None
    report = edited(tmp_path, base, lift)
    assert report.ok, report.problems
    row = report.summary['materials']['TEST_A']
    assert row['triangles'] == [8, 8] and row['area_m2'][1] > row['area_m2'][0] == 4.0


def test_same_geometry_ignores_order(tmp_path):
    pos, ids, tris = grid(0, 2)
    write_glb(tmp_path / 'a.glb', [(0, pos, ids, tris)])
    write_glb(tmp_path / 'b.glb', [(0, pos, ids, list(reversed(tris)))])
    assert chunk_glb.same_geometry(tmp_path / 'a.glb', tmp_path / 'b.glb', full=True) == []
    write_glb(tmp_path / 'c.glb', [(1, pos, ids, tris)])
    assert chunk_glb.same_geometry(tmp_path / 'a.glb', tmp_path / 'c.glb')


# --- Blender

def have_blender():
    try:
        return bool(world_edit.blender_binary())
    except world_edit.EditError:
        return False


blender = pytest.mark.skipif(not have_blender(), reason='no Blender')


def build(tmp_path, base, *args, record_id='test-chunk'):
    staging, registry = tmp_path / 'staging', tmp_path / 'world-edits.jsonl'
    argv = ['build', record_id, '--base', str(base), '--staging', str(staging), '--registry', str(registry),
            '--work', str(tmp_path / 'work'), *args]
    return world_edit.main(argv), staging, registry


def edit_args(*names):
    return [x for n in names for x in ('--edit', str(EDITS / f'{n}.py'))]


@blender
@pytest.mark.parametrize('name, reason', [('move_border', 'border vertex'), ('delete_face', 'new open edge'),
                                          ('strip_with_gap', 'new open edge'), ('unknown_material', 'unknown material')])
def test_build_refuses(tmp_path, capsys, name, reason):
    base = world(tmp_path)
    code, staging, registry = build(tmp_path, base, '--set', 'test-1', '--chunk', 'TESTWORLD/0_0', *edit_args(name))
    out = capsys.readouterr().out
    assert code == 1
    assert f'FAIL: {reason}' in out
    assert not registry.exists() and not (staging / 'test-1').exists()


@blender
def test_build_adds_edits_up_and_records_them(tmp_path, capsys):
    base = world(tmp_path)
    code, staging, registry = build(tmp_path, base, '--set', 'test-1', '--chunk', 'TESTWORLD/0_0',
                                    *edit_args('raise_middle'))
    assert code == 0, capsys.readouterr()
    # a second edit of another step goes on the same base chunk after the first
    code, staging, registry = build(tmp_path, base, *edit_args('add_post'))
    assert code == 0, capsys.readouterr()
    entry = json.loads(registry.read_text())
    assert [e['script'] for e in entry['edits']] == ['scripts/content/world/test_edits/raise_middle.py',
                                                     'scripts/content/world/test_edits/add_post.py']
    assert entry['edits'][0]['parameters'] == {'lift_cm': 5}
    assert entry['edits'][1]['open_edges_declared'] == 3
    assert entry['inputs'][0]['sha256'] == world_edit.sha256(base / 'worlds/TESTWORLD/chunks/0_0.glb')
    out = staging / 'test-1/worlds/TESTWORLD/chunks/0_0.glb'
    assert entry['files']['glb'] == {'path': 'test-1/worlds/TESTWORLD/chunks/0_0.glb', 'sha256': world_edit.sha256(out)}
    chunk = chunk_glb.read_chunk(out)
    positions, _ = chunk.corners()
    assert (np.abs(positions - [100, 5, 100]).max(1) < 1e-4).any()       # raise_middle
    assert chunk.triangles == 9                                            # add_post
    # a rebuild of the same list leaves the published chunk as it is
    before = out.stat().st_mtime_ns
    code, _, _ = build(tmp_path, base)
    assert code == 0 and out.stat().st_mtime_ns == before
    assert 'left as it is' in capsys.readouterr().out


# --- rebuilds of the published chunks

def published(record_id):
    entries = {e['id']: e for e in world_edit.read_registry(world_edit.REGISTRY)}
    entry = entries[record_id]
    try:
        base = world_edit.resolve_base(None)
    except world_edit.EditError:
        pytest.skip('no base')
    chunk = world_edit.STAGING / entry['files']['glb']['path']
    if not chunk.exists() or not (base / 'worlds').is_dir():
        pytest.skip(f'{chunk} is not on this machine')
    return entry, chunk


@blender
def test_rebuild_harbour_stairs_is_byte_equal(tmp_path):
    entry, chunk = published('harbour-stairs-newworld-1-1')
    assert world_edit.main(['build', entry['id'], '--dry-run', '--work', str(tmp_path)]) == 0
    assert world_edit.sha256(tmp_path / 'out.glb') == world_edit.sha256(chunk) == entry['files']['glb']['sha256']


@blender
def test_rebuild_roof_holes_is_the_same_geometry(tmp_path):
    entry, chunk = published('harbour-roof-holes-newworld-0-1')
    assert world_edit.main(['build', entry['id'], '--dry-run', '--work', str(tmp_path)]) == 0
    # the exact boolean orders the cut roof's vertices differently from run to run: compare the triangles
    assert chunk_glb.same_geometry(tmp_path / 'out.glb', chunk, full=True) == []
    assert world_edit.sha256(chunk) == entry['files']['glb']['sha256']


@blender
@pytest.mark.parametrize('record_id, hole', [('harbour-stairs-newworld-1-1', 'delete_largest_stair'),
                                             ('harbour-roof-holes-newworld-0-1', 'delete_hole_walls')])
def test_a_hole_in_the_new_geometry_is_caught(tmp_path, capsys, record_id, hole):
    entry, _ = published(record_id)
    args = ['build', 'test-hole', '--set', 'test-hole', '--chunk', entry['replaces'][0], '--from', record_id,
            '--edit', str(EDITS / f'{hole}.py'), '--dry-run', '--work', str(tmp_path)]
    for m in entry.get('materials_from', []):
        args += ['--materials', str(world_edit.STAGING / m)]
    assert world_edit.main(args) == 1
    assert 'FAIL: new open edge' in capsys.readouterr().out
