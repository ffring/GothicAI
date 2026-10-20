"""Build a framed plank crate by parts from an image_gen concept and a numeric target.

blender -b --factory-startup -P crate.py -- crate.json OUT_DIR
config: {"id": "crate", "target": mesh3d-pilot.json (default: the repo's), "seed": 1,
         "frame": 0.09, "batten": 0.025, "raise": 0.012, "plank": 0.02, "gap": 0.006,
         "planks": {"long": 6, "short": 6, "top": 7}, "width_jitter": 0.12, "proud": 0.002,
         "plate_leg": 0.12, "plate_width": 0.06, "plate": 0.004, "nail": 0.004, "nail_radius": 0.008,
         "nails_per_plate": 2, "plank_nails": true, "stave_tile": 0.38, "planks_per_tile": 4}
Part counts are read from the concept by eye; the target gives size, origin and
budget. The outermost points (nail heads) meet the target bbox. Every plank,
batten, plate and nail is its own closed part with a "_part" number, so
bake_atlas.py can tone each one; tile UVs are in metres with the grain along
the part's length, and each board takes one plank of the wood tile.
"""
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
cfg = json.loads(Path(args[0]).read_text())
out = Path(args[1])
out.mkdir(parents=True, exist_ok=True)
repo = Path(__file__).resolve().parents[4]
targets = json.loads(Path(cfg.get("target", repo / "data/content/mesh3d-pilot.json")).read_text())
targets = targets["targets"] if isinstance(targets, dict) else targets
goal = next(t for t in targets if t["id"] == cfg["id"])
rng = np.random.default_rng(cfg.get("seed", 1))
zones = {z["key"]: z for z in goal["zones"]}
keys = list(zones)
PLANK, BRACE, NAIL, BAND = (keys.index(next(k for k in keys if k.endswith(s))) for s in ("PLANK", "BRACE", "NAIL", "BAND"))

# glTF metres, +Y up -> Blender X = X, Y = -Z, Z = Y.
gmin, gmax = np.array(goal["bbox_min"]), np.array(goal["bbox_max"])
lo = Vector((gmin[0], -gmax[2], gmin[1]))
hi = Vector((gmax[0], -gmin[2], gmax[1]))
centre = (lo + hi) / 2
half = (hi - lo) / 2

tile = cfg.get("stave_tile", 0.38)
plank_u = tile / cfg.get("planks_per_tile", 4)
nail_h = cfg.get("nail", 0.004)
plate_t = cfg.get("plate", 0.004)
m = nail_h + plate_t                      # batten outer face, inward from the bbox
fw = cfg.get("frame", 0.09)
batten_t = cfg.get("batten", 0.025)
plank_out = m + cfg.get("raise", 0.012)   # plank outer face, inward from the bbox
plank_t = cfg.get("plank", 0.02)
gap = cfg.get("gap", 0.006)

bm = bmesh.new()
uv = bm.loops.layers.uv.new("UVMap")
part_layer = bm.verts.layers.float.new("_part")
parts = [0]


def new_part():
    parts[0] += 1
    return parts[0] - 1


def poly(verts, material, uvs):
    face = bm.faces.new(verts)
    face.material_index = material
    for loop, coord in zip(face.loops, uvs):
        loop[uv].uv = coord
    return face


def board(c, ax_len, ax_w, ax_n, length, width, thick, material, plank=None, v_off=0.0):
    """A closed box; grain along ax_len. The face along ax_n takes one plank of the tile."""
    part = new_part()
    corner = {}
    for sl in (-1, 1):
        for sw in (-1, 1):
            for sn in (-1, 1):
                v = bm.verts.new(c + ax_len * (sl * length / 2) + ax_w * (sw * width / 2) + ax_n * (sn * thick / 2))
                v[part_layer] = part
                corner[sl, sw, sn] = v
    local = {v: (sl * length / 2, sw * width / 2, sn * thick / 2) for (sl, sw, sn), v in corner.items()}

    def coords(v, axis):
        l, w, n = local[v]
        if axis == "n":  # the board's face: across = one plank of the tile, along = grain
            k = plank if plank is not None else 0
            return (k * plank_u + (w / width + 0.5) * plank_u, l + v_off)
        if axis == "w":
            return (n, l + v_off)
        return (w, n)

    for axis, index in (("l", 0), ("w", 1), ("n", 2)):
        for sign in (-1, 1):
            quad = [v for key, v in corner.items() if key[index] == sign]
            # order the four corners around the face
            others = [i for i in range(3) if i != index]
            ring = sorted(quad, key=lambda v: math.atan2(local[v][others[1]], local[v][others[0]]))
            poly(ring, material, [coords(v, axis) for v in ring])


def plate_l(c, ax_a, ax_b, ax_n, leg, width, thick, material):
    """Flat L-shaped plate in the corner at c, legs along +ax_a and +ax_b, raised along ax_n."""
    part = new_part()
    # Seven points: (0, width) splits the concave L into two convex faces for triangulation.
    outline = [(0, 0), (leg, 0), (leg, width), (width, width), (width, leg), (0, leg), (0, width)]
    rings = []
    for d in (0.0, thick):
        ring = []
        for a, b in outline:
            v = bm.verts.new(c + ax_a * a + ax_b * b + ax_n * d)
            v[part_layer] = part
            ring.append(v)
        rings.append(ring)
    low, top = rings
    for idx in ((0, 1, 2, 3, 6), (6, 3, 4, 5)):
        poly([top[i] for i in idx], material, [outline[i] for i in idx])
        poly([low[i] for i in idx[::-1]], material, [outline[i] for i in idx[::-1]])
    n = len(outline)
    for i in range(n):
        j = (i + 1) % n
        poly((low[i], low[j], top[j], top[i]), material,
             ((i * 0.02, 0), ((i + 1) * 0.02, 0), ((i + 1) * 0.02, thick), (i * 0.02, thick)))


nail_count = [0]


def nail(c, ax_a, ax_b, ax_n, radius, height, material, sink=0.001):
    """A three-sided nail head sunk into the wood; its open base is hidden, so it costs 3 triangles."""
    part = new_part()
    nail_count[0] += 1
    base = []
    offset = rng.uniform(0, 2 * math.pi)
    for i in range(3):
        t = 2 * math.pi * i / 3 + offset
        v = bm.verts.new(c + (ax_a * math.cos(t) + ax_b * math.sin(t)) * radius - ax_n * sink)
        v[part_layer] = part
        base.append(v)
    tip = bm.verts.new(c + ax_n * height)
    tip[part_layer] = part
    for i in range(3):
        poly((base[i], base[(i + 1) % 3], tip), material, [(0, 0), (radius, 0), (radius / 2, height)])


X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
# Faces: outward normal, plank direction, other in-plane axis, plank count, priority at shared edges.
# The bottom stands on the ground and is not built; sides run down to the bbox floor.
faces = [
    (Y, X, Z, cfg["planks"]["long"], 0), (-Y, X, Z, cfg["planks"]["long"], 0),
    (X, Y, Z, cfg["planks"]["short"], 1), (-X, Y, Z, cfg["planks"]["short"], 1),
    (Z, X, Y, cfg["planks"]["top"], 2),
]
priority = {tuple(f[0]): f[4] for f in faces}


def extent(axis):
    return abs(half.dot(axis))


def side_range(axis, own):
    """Range along an in-plane axis: the higher-priority neighbour keeps the shared edge, so
    coplanar batten faces never overlap; toward the missing bottom the face reaches the ground."""
    e = extent(axis)
    ends = []
    for sign in (-1, 1):
        neighbour = tuple(axis * sign)
        if neighbour == (0.0, 0.0, -1.0) or neighbour == (-0.0, -0.0, -1.0):
            ends.append(e)
        elif priority.get(neighbour, -1) > own:
            ends.append(e - m - batten_t)
        else:
            ends.append(e - m)
    return -ends[0], ends[1]


# Dark core behind the plank gaps.
core_in = plank_out + plank_t + 0.002
core = [bm.verts.new(centre + Vector((sx * (half.x - core_in), sy * (half.y - core_in), sz * (half.z - core_in))))
        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
core_part = new_part()
for v in core:
    v[part_layer] = core_part
for quad in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
    poly([core[i] for i in quad], PLANK, [(0, 0), (1, 0), (1, 1), (0, 1)])

width_jitter = cfg.get("width_jitter", 0.12)
proud = cfg.get("proud", 0.002)
for normal, along, across, count, own in faces:
    a_lo, a_hi = side_range(along, own)
    b_lo, b_hi = side_range(across, own)
    face_plane = centre + normal * extent(normal)
    outer = face_plane - normal * (m + batten_t / 2)
    a_mid, b_mid = (a_lo + a_hi) / 2, (b_lo + b_hi) / 2
    # Battens: two along the planks over the whole face, two across between them.
    for b in (b_lo + fw / 2, b_hi - fw / 2):
        board(outer + along * a_mid + across * b, along, across, normal, a_hi - a_lo, fw, batten_t, BRACE,
              int(rng.integers(0, 4)), rng.uniform(0, tile))
    for a in (a_lo + fw / 2, a_hi - fw / 2):
        board(outer + along * a + across * b_mid, across, along, normal, b_hi - b_lo - 2 * fw, fw, batten_t, BRACE,
              int(rng.integers(0, 4)), rng.uniform(0, tile))
    # Planks inside the frame, 1 cm under the battens' inner edges.
    span = b_hi - b_lo - 2 * fw + 0.02
    length = a_hi - a_lo - 2 * fw + 0.02
    widths = 1 + width_jitter * rng.uniform(-1, 1, count)
    widths = widths / widths.sum() * (span - gap * (count - 1))
    b = b_lo + fw - 0.01
    for w in widths:
        depth = plank_out + plank_t / 2 + rng.uniform(-proud, proud)
        c = face_plane - normal * depth + along * a_mid + across * (b + w / 2)
        board(c, along, across, normal, length, w, plank_t, PLANK, int(rng.integers(0, 4)), rng.uniform(0, tile))
        if cfg.get("plank_nails", True):  # one nail through each end batten into the plank
            for a in (a_lo + fw / 2, a_hi - fw / 2):
                spot = face_plane - normal * m + along * a + across * (b + w / 2)
                nail(spot, along, across, normal, cfg.get("nail_radius", 0.008), nail_h, NAIL)
        b += w + gap

# Corner plates: an L-plate on each visible face around the 8 corners, nails on each.
leg, pw = cfg.get("plate_leg", 0.12), cfg.get("plate_width", 0.06)
nails = cfg.get("nails_per_plate", 2)
axis = {"x": X, "y": Y, "z": Z}
for sx in (-1, 1):
    for sy in (-1, 1):
        for sz in (-1, 1):
            signs = {"x": sx, "y": sy, "z": sz}
            corner_point = centre + Vector((sx * (half.x - m), sy * (half.y - m), sz * (half.z - m)))
            if sz < 0:
                corner_point.z = centre.z - half.z + 0.01  # the lower plates end just above the ground
            for normal_axis, a_axis, b_axis in (("x", "y", "z"), ("y", "x", "z"), ("z", "x", "y")):
                if normal_axis == "z" and sz < 0:
                    continue
                n = axis[normal_axis] * signs[normal_axis]
                a = -axis[a_axis] * signs[a_axis]   # legs run inward from the corner
                b = -axis[b_axis] * signs[b_axis]
                if a.cross(b).dot(n) < 0:
                    a, b = b, a
                plate_l(corner_point.copy(), a, b, n, leg, pw, plate_t, BAND)
                for i in range(nails):
                    along_leg = leg * (0.35 + 0.45 * i) if nails > 1 else leg * 0.5
                    first, second = (a, b) if i % 2 == 0 else (b, a)
                    spot = corner_point + n * plate_t + first * along_leg + second * (pw / 2)
                    nail(spot, a, b, n, cfg.get("nail_radius", 0.008), nail_h, NAIL)

bmesh.ops.triangulate(bm, faces=bm.faces)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
open_edges = sum(1 for e in bm.edges if not e.is_manifold)
mesh = bpy.data.meshes.new(goal["id"])
bm.to_mesh(mesh)
bm.free()
mesh.validate()
mesh.update()
part_attr = mesh.attributes.get("_part")
for key in keys:
    mat = bpy.data.materials.new(key)
    mat["group"] = zones[key]["group"]
    mesh.materials.append(mat)
mesh.shade_smooth()
mesh.set_sharp_from_angle(angle=math.radians(30))
obj = bpy.data.objects.new(goal["id"], mesh)
bpy.context.scene.collection.objects.link(obj)
for o in list(bpy.context.scene.objects):
    o.select_set(o == obj)
bpy.context.view_layer.objects.active = obj
bpy.ops.export_scene.gltf(filepath=str(out / "model.glb"), export_format="GLB", use_selection=True,
                          export_apply=True, export_yup=True, export_extras=True, export_normals=True,
                          export_texcoords=True, export_animations=False, export_attributes=True)
blo = np.min([c[:] for c in obj.bound_box], axis=0)
bhi = np.max([c[:] for c in obj.bound_box], axis=0)
metrics = {"triangles": len(mesh.polygons), "budget": goal["budget_tris"],
           "open_edges": open_edges, "open_edges_expected": 3 * nail_count[0],
           "parts": parts[0], "has_part_attribute": part_attr is not None,
           "bbox_delta_min_m": (blo - np.array(lo)).round(4).tolist(), "bbox_delta_max_m": (bhi - np.array(hi)).round(4).tolist(),
           "blender_version": bpy.app.version_string}
(out / "metrics.json").write_text(json.dumps(metrics, indent=2))
print("BUILT", json.dumps(metrics))
