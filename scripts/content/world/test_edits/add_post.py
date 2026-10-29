"""Test edit: adds a free-standing triangle and declares its open edges (allowed)."""
import bmesh
from mathutils import Vector


def edit(ctx):
    bm = bmesh.new()
    bm.from_mesh(ctx.mesh)
    vs = [bm.verts.new(Vector(p)) for p in ((50, -50, 0), (60, -50, 0), (50, -50, 30))]
    f = bm.faces.new(vs)
    f.material_index = 1
    ctx.allow_open([f], 'a post standing on its own')
    bm.to_mesh(ctx.mesh)
    bm.free()
