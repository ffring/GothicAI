"""The Blender side of world_edit.py: apply a list of edits to a base chunk of the converted world mesh.

Blender 5.2, started by world_edit.py (world_edit_blender.py is the entry):

    Blender -b --factory-startup --python scripts/content/world/world_edit_blender.py -- <job.json>

job.json: {"base": <base chunk .glb>, "out": <edited .glb>, "allowed": <allowed.json>, "meta": <meta.json>,
"edits": [<edit .py>, ...], "names": {<edit .py>: <path in git>}, "world": <WORLD>, "repo": <repository>,
"chunk": <x>_<z>, "base_chunks": <the base's chunks folder>, "gui": false}. The base chunk is imported twice: the chunk the edits change, and the original as an underlay to
measure against (wireframe, cannot be selected, not rendered). Each edit is a script with one function,
`edit(ctx)`, run in order on the same mesh, so edits of several steps in one chunk add up. The underlay is removed
before the export (and before any save of a .blend in the GUI). allowed.json gets the open edges each edit
declared with ctx.allow_open, in glTF centimetres, for chunk_glb.check; meta.json the PARAMETERS and INPUTS
(data files) of each edit, for the registry.
"""
import importlib.util
import json
import struct
from pathlib import Path

import bpy
from mathutils import Vector

UNDERLAY = 'underlay'


def to_gothic(v):
    # glTF stores (-x, y, z) cm; Blender's importer turns glTF (x, y, z) into (x, -z, y).
    return Vector((-v.x, v.z, -v.y))


def to_blender(g):
    return Vector((-g.x, -g.z, g.y))


def to_gltf(v):
    return (v.x, v.z, -v.y)


def gothic_name(material):
    record = material.get('gothic') if material else None
    return record.get('name') if record else (material.name if material else '')


class Context:
    """What an edit gets: the chunk's object and mesh, the underlay, the chunk's name, the base's chunks folder
    (to read a neighbour: something that lies across the chunk border), and allow_open."""

    def __init__(self, obj, underlay, world, chunk, base_chunks=None):
        self.obj = obj
        self.mesh = obj.data
        self.underlay = underlay
        self.world = world
        self.chunk = chunk
        self.base_chunks = Path(base_chunks) if base_chunks else None
        self.edit = None
        self.allowed = []     # [{'edit', 'reason', 'edges': [[a, b], ...]}]

    def allow_open(self, faces, reason):
        """Declare that the outline of these bmesh faces may be open in the result: the outline of faces the edit
        removes, or the free sides of what it builds (the sides of a stair, a beam standing on its own). Only the
        outline is declared: an edge two faces of the set share, by exact position as the check welds them, stays
        checked, so a hole inside the new geometry is still caught. Positions are taken now, so call it while the
        faces are valid and placed."""
        count, ends = {}, {}
        for f in faces:
            for e in f.edges:
                a, b = (tuple(struct.unpack('3f', struct.pack('3f', *to_gltf(v.co)))) for v in e.verts)
                key = (a, b) if a <= b else (b, a)
                count[key] = count.get(key, 0) + 1
                ends[key] = [a, b]
        edges = [ends[k] for k, n in count.items() if n == 1]
        self.allowed.append({'edit': self.edit, 'reason': reason, 'edges': edges})


def load_edit(path):
    spec = importlib.util.spec_from_file_location(f'world_edit_{Path(path).stem}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, 'edit', None)):
        raise SystemExit(f'{path}: an edit needs a function edit(ctx)')
    return module


def import_chunk(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    return next(o for o in bpy.data.objects if o not in before and o.type == 'MESH')


def make_underlay(obj):
    obj.name = UNDERLAY
    obj.data.name = UNDERLAY
    obj.hide_select = True
    obj.hide_render = True
    obj.display_type = 'WIRE'
    return obj


def remove_underlay():
    obj = bpy.data.objects.get(UNDERLAY)
    if obj is None:
        return
    mesh = obj.data
    materials = [m for m in mesh.materials if m]
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    for m in materials:
        if m.users == 0:
            bpy.data.materials.remove(m)


def remove_underlay_on_save(*_):
    remove_underlay()


def main(job_path):
    job = json.loads(Path(job_path).read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = import_chunk(job['base'])
    underlay = make_underlay(import_chunk(job['base']))
    world, chunk = job.get('world', ''), job.get('chunk') or Path(job['base']).stem
    ctx = Context(obj, underlay, world, chunk, job.get('base_chunks'))
    meta = {}
    for path in job['edits']:
        ctx.edit = job.get('names', {}).get(path, path)
        print(f'edit {ctx.edit}')
        module = load_edit(path)
        module.edit(ctx)
        # what the registry keeps of the edit besides its script: its constants and the data files it read
        meta[ctx.edit] = {'parameters': getattr(module, 'PARAMETERS', None),
                          'inputs': [Path(p).resolve().relative_to(job['repo']).as_posix()
                                     for p in getattr(module, 'INPUTS', [])]}
    if job.get('gui'):
        bpy.app.handlers.save_pre.append(remove_underlay_on_save)
        return
    remove_underlay()
    Path(job['allowed']).write_text(json.dumps({'edits': ctx.allowed}) + '\n')
    Path(job['meta']).write_text(json.dumps(meta) + '\n')
    tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)
    print(f'chunk: {len(obj.data.polygons)} faces, {tris} triangles after the edits')
    for o in bpy.data.objects:
        o.select_set(o == obj)
    bpy.ops.export_scene.gltf(filepath=job['out'], export_format='GLB', use_selection=True, export_extras=True,
                              export_attributes=True, export_vertex_color='ACTIVE', export_all_vertex_colors=True)
