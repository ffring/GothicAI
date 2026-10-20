"""Build the canvas roof of a market stall by parts from an image_gen concept and a numeric target.

blender -b --factory-startup -P stall-roof.py -- stall-roof.json OUT_DIR   (run from the repository root)
config: {"id": "stall-roof", "target": "data/content/mesh3d-market.json", "seed": 1, "posts": 3, "post": 0.1,
         "arm": [0.08, 0.1, 0.1], "brace": [0.6, -0.1, 0.07], "pole": [0.04, 8], "battens": 1, "batten": [0.04, 0.025],
         "canvas": [0.06, 0.18, 6, 6], "cloth": 0.006, "valance": [9, 4, 0.16, 0.08], "stripes": [[0.035, 0.06], [0.075, 0.095]],
         "lash": [0.012, 0.05], "cloth_tint": [0.36, 0.29, 0.15], "tiles": "market-set/tiles", "stave_tile": 0.38, "planks_per_tile": 4}
The stall roof stands on the sales table (a vob of its own) with its posts at the back,
-Y in Blender (glTF +Z), as the original's: three square posts carry sloping arms that
reach forward to the front edge, each with a diagonal brace. Two round poles of barked
wood run the full length on the arms, back and front; a sheet of canvas lies between them
on the arms and sags between the poles, deepest midway between two arms, where a curved
batten under it stops it ("battens": 1 puts one midway between each two arms). Over the front pole the canvas hangs down as a valance cut into scallops, with two
woven stripes along it. Ropes lash the poles to the arms. The poles, the stripes and the
ropes are zones of their own ("zones" in the config): bark, red band and rope tiles.
"""
import math
import shutil
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parts import X, Y, Z, Parts, args, load_goal  # noqa: E402

cfg, out = args()
goal, lo, hi = load_goal(cfg)
rng = np.random.default_rng(cfg.get("seed", 1))
keys = [z["key"] for z in goal["zones"]]
FRAME, CLOTH, ROPE, BARK, BAND = (keys.index(k) for k in ("OWN_PROP_WOOD_FRAME", "OWN_PROP_CLOTH", "OWN_PROP_ROPE",
                                                          "OWN_PROP_BARK", "OWN_PROP_CLOTH_BAND"))
tile = cfg.get("stave_tile", 0.38)
P = Parts(rng, tile / cfg.get("planks_per_tile", 4))
staging = Path.home() / ".local/share/gothic-ai/content-staging"


def tint(png, gain):
    """Scale an sRGB PNG's linear colour by gain per channel, in place."""
    image = bpy.data.images.load(str(png))
    image.colorspace_settings.name = "Non-Color"           # raw stored values
    px = np.empty(len(image.pixels), np.float32)
    image.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    rgb = px[:, :3]
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4) * np.array(gain, np.float32)
    px[:, :3] = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(np.maximum(lin, 0), 1 / 2.4) - 0.055)
    image.pixels.foreach_set(px.ravel())
    image.filepath_raw = str(png)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def tiles():
    """OUT_DIR/tiles for the atlas bake: shared tiles, the canvas tile under both cloth keys.
    bake_atlas.py leaves faces that look straight down at their tile colour (it takes them for
    the bottom a prop stands on), but the canvas's underside is seen from the stall: so the
    canvas copy is tinted to about its measured colour first ("cloth_tint", the gain one bake
    gave) and both sides of the canvas end up alike."""
    folder = out / "tiles"
    source = staging / cfg.get("tiles", "market-set/tiles")
    for key, src in (("own_prop_wood_frame", "own_prop_wood_frame"), ("own_prop_bark", "own_prop_bark"),
                     ("own_prop_rope", "own_prop_rope"), ("own_prop_cloth", "own_prop_canvas"),
                     ("own_prop_cloth_band", "own_prop_canvas")):
        shutil.rmtree(folder / key, ignore_errors=True)
        shutil.copytree(source / src, folder / key)
    if "cloth_tint" in cfg:
        tint(folder / "own_prop_cloth" / "albedo.png", cfg["cloth_tint"])


def beam(a, b, width, height, material):
    """A square-section timber from point a to point b, its height vertical as far as it can be."""
    d = (b - a).normalized()
    side = d.cross(Z).normalized() if abs(d.z) < 0.95 else X
    up = side.cross(d).normalized()
    P.post(b, a, width, width, side, up, material, tile) if height == width else \
        P.wood((a + b) / 2, d, up, side, (b - a).length, height, width, material, tile)


def prism(c, axis, radius, length, sides, material, u_scale=1.0):
    """A closed capped n-sided prism along axis through c (copied from cart.py); U around, V along."""
    part = P.new_part()
    a = axis.normalized()
    b = (X if abs(a.dot(X)) < 0.9 else Y).cross(a).normalized()
    d = a.cross(b)
    ends = [[P.vert(c + a * (s * length / 2) + (b * math.cos(2 * math.pi * i / sides)
                                                + d * math.sin(2 * math.pi * i / sides)) * radius, part)
             for i in range(sides)] for s in (-1, 1)]
    edge = 2 * math.pi * radius / sides * u_scale
    for i in range(sides):
        j = (i + 1) % sides
        P.poly((ends[0][i], ends[0][j], ends[1][j], ends[1][i]), material,
               ((i * edge, 0), ((i + 1) * edge, 0), ((i + 1) * edge, length), (i * edge, length)))
    for ring_, sign in ((ends[0], -1), (ends[1], 1)):
        verts = ring_ if sign > 0 else ring_[::-1]
        P.poly(verts, material, [((v.co - c).dot(b), (v.co - c).dot(d)) for v in verts])


def ring(c, axis, r_in, r_out, width, segments, material):
    """A closed ring of rectangular section around axis through c (copied from cart.py): a rope lashing."""
    part = P.new_part()
    a = axis.normalized()
    b = (X if abs(a.dot(X)) < 0.9 else Y).cross(a).normalized()
    d = a.cross(b)
    rows = []
    for i in range(segments):
        t = 2 * math.pi * i / segments
        radial = b * math.cos(t) + d * math.sin(t)
        rows.append([P.vert(c + radial * r + a * (s * width / 2), part)
                     for r, s in ((r_in, -1), (r_out, -1), (r_out, 1), (r_in, 1))])
    arc = 2 * math.pi / segments
    for i in range(segments):
        j = (i + 1) % segments
        u0, u1 = i * arc * r_out, (i + 1) * arc * r_out
        for k in range(4):
            m = (k + 1) % 4
            side = (r_in if k in (0, 3) else r_out) if k % 2 == 0 else width
            P.poly((rows[i][k], rows[j][k], rows[j][m], rows[i][m]), material,
                   ((u0, 0), (u1, 0), (u1, side), (u0, side)))


def sheet(points, normals, uvs, thick, row_material):
    """A closed sheet of cloth `thick` thick over a grid of points (rows x cols) offset along normals."""
    part = P.new_part()
    rows, cols = len(points), len(points[0])
    v = {(r, c, s): P.vert(points[r][c] + normals[r][c] * (s * thick / 2), part)
         for r in range(rows) for c in range(cols) for s in (-1, 1)}

    def quad(keys_, material):
        P.poly([v[k] for k in keys_], material, [uvs[k[0]][k[1]] for k in keys_])

    for r in range(rows - 1):
        m = row_material(r)
        for c in range(cols - 1):
            quad(((r, c, 1), (r, c + 1, 1), (r + 1, c + 1, 1), (r + 1, c, 1)), m)
            quad(((r, c + 1, -1), (r, c, -1), (r + 1, c, -1), (r + 1, c + 1, -1)), m)
        for c in (0, cols - 1):
            quad(((r, c, -1), (r, c, 1), (r + 1, c, 1), (r + 1, c, -1)), m)
    for r in (0, rows - 1):
        for c in range(cols - 1):
            quad(((r, c, 1), (r, c, -1), (r, c + 1, -1), (r, c + 1, 1)), row_material(min(r, rows - 2)))


def grid_normals(points):
    """Unit normals of a grid surface from its neighbours (rows run along v, cols along u)."""
    rows, cols = len(points), len(points[0])
    normals = []
    for r in range(rows):
        line = []
        for c in range(cols):
            du = points[r][min(c + 1, cols - 1)] - points[r][max(c - 1, 0)]
            dv = points[min(r + 1, rows - 1)][c] - points[max(r - 1, 0)][c]
            line.append(du.cross(dv).normalized())
        normals.append(line)
    return normals


# --- layout: X along the stall, Y toward the customer, Z up; posts at the back ---
tiles()
pw = cfg.get("post", 0.1)
arm_w, arm_h, arm_drop = cfg.get("arm", [0.08, 0.1, 0.1])
pole_r, pole_sides = cfg.get("pole", [0.04, 8])
cloth = cfg.get("cloth", 0.006)
lash_t, lash_w = cfg.get("lash", [0.012, 0.05])
x_post = hi.x - pw / 2
y_post = lo.y + pw / 2
y_back_pole = lo.y + pole_r + 0.03
y_front_pole = hi.y - cloth - pole_r - lash_t     # the lashing and the valance stand in front of it
z_back_top = hi.z - lash_t - 2 * pole_r           # arm top at the back pole; the lashing meets the bbox
z_front_top = z_back_top - arm_drop               # the arm slopes down to the front
post_top = z_back_top - arm_h


def arm_top(y):
    t = (y - lo.y) / (y_front_pole + pole_r - lo.y)
    return z_back_top + (z_front_top - z_back_top) * t


xs = np.linspace(-x_post, x_post, cfg.get("posts", 3))
brace_z, brace_y, brace_w = cfg.get("brace", [0.3, -0.1, 0.07])
for x in xs:
    xi = float(x)
    beam(Vector((xi, y_post, lo.z)), Vector((xi, y_post, post_top)), pw, pw, FRAME)
    # The arm rests on the post and runs forward under both poles.
    a = Vector((xi, lo.y + 0.005, arm_top(lo.y) - arm_h / 2))   # its sloping end stays inside the bbox
    b = Vector((xi, y_front_pole + pole_r, arm_top(y_front_pole + pole_r) - arm_h / 2))
    beam(a, b, arm_w, arm_h, FRAME)
    # Diagonal brace from the post up to the arm's underside.
    p0 = Vector((xi, y_post + pw / 2 - 0.01, lo.z + brace_z))
    p1 = Vector((xi, brace_y, arm_top(brace_y) - arm_h + 0.01))
    beam(p0, p1, brace_w, brace_w, FRAME)
# Two barked poles over the full length.
poles = []
for y in (y_back_pole, y_front_pole):
    zc = arm_top(y) + pole_r
    prism(Vector((0, y, zc)), X, pole_r, hi.x - lo.x, pole_sides, BARK, u_scale=1.6)
    poles.append(Vector((0, y, zc)))
for x in xs:
    for p in poles:
        ring(Vector((float(x), p.y, p.z)), X, pole_r - 0.004, pole_r + lash_t, lash_w, 6, ROPE)

# Canvas from the top of the back pole to the top of the front pole. Over an arm it rests
# on the arm and sags only a little; between two arms it sags deeper, down onto the curved
# batten midway ("canvas": [sag over an arm, sag midway, columns per half span, rows]).
sag_arm, sag_mid, cols_per_span, rows_n = cfg.get("canvas", [0.06, 0.18, 6, 6])
arms = [float(x) for x in xs]
battens = [float(x) for x in (xs[:-1] + xs[1:]) / 2] if cfg.get("battens", 1) else []
supports = sorted(arms + battens)
x_edges = [lo.x + 0.01] + supports + [hi.x - 0.01]
x_cols = []
for a, b in zip(x_edges[:-1], x_edges[1:]):
    n = cols_per_span if a in supports and b in supports else 2
    x_cols += list(np.linspace(a, b, n + 1)[:-1])
x_cols.append(x_edges[-1])


def sag(x):
    """The canvas's sag midway between the poles at x: sag_arm over an arm, sag_mid midway between two."""
    for a, b in zip(arms[:-1], arms[1:]):
        if a <= x <= b:
            return sag_arm + (sag_mid - sag_arm) * math.sin(math.pi * (x - a) / (b - a)) ** 2
    return sag_arm                                      # the ends hang over the outer arms


back, front = poles[0] + Vector((0, 0, pole_r)), poles[1] + Vector((0, 0, pole_r))
pts, uvs = [], []
for r in range(rows_n + 1):
    t = r / rows_n
    row, uv_row = [], []
    for x in x_cols:
        base = back.lerp(front, t)
        row.append(Vector((x, base.y, base.z - sag(x) * math.sin(math.pi * t))))
        uv_row.append((x, t * (front - back).length * 1.05))
    pts.append(row)
    uvs.append(uv_row)
sheet(pts, grid_normals(pts), uvs, cloth, lambda r: CLOTH)
# Curved battens under the canvas midway between the arms.
bw, bh = cfg.get("batten", [0.04, 0.025])
for x in battens:
    steps = rows_n
    chain = []
    for k in range(steps + 1):
        t = k / steps
        base = back.lerp(front, t)
        chain.append(Vector((x, base.y, base.z - sag(x) * math.sin(math.pi * t) - cloth - bh / 2)))
    for p, q in zip(chain[:-1], chain[1:]):
        beam(p, q, bw, bh, FRAME)

# Valance over the front pole: scallops along the bottom, two woven stripes.
count, per, d0, d1 = cfg.get("valance", [9, 4, 0.16, 0.08])
stripes = cfg.get("stripes", [[0.03, 0.05], [0.07, 0.085]])
yv = poles[1].y + pole_r + lash_t + cloth / 2
zt = poles[1].z + pole_r
length = hi.x - lo.x - 0.02
cols = [lo.x + 0.01 + length * k / (count * per) for k in range(count * per + 1)]
depths = sorted({0.0, *[d for s in stripes for d in s]})
pts, uvs = [], []
for d in depths + [None]:
    row, uv_row = [], []
    for k, x in enumerate(cols):
        f = (k % per) / per
        bottom = d0 + d1 * math.sin(math.pi * f)
        z = zt - (bottom if d is None else d)
        row.append(Vector((x, yv, z)))
        uv_row.append((x, zt - z))
    pts.append(row)
    uvs.append(uv_row)
normals = [[Y for _ in row] for row in pts]
stripe_rows = {i for i, d in enumerate(depths) if any(abs(d - s[0]) < 1e-9 for s in stripes)}
sheet(pts, normals, uvs, cloth, lambda r: BAND if r in stripe_rows else CLOTH)

P.finish(goal, out, lo, hi, {"supports_x": [round(x, 3) for x in supports], "scallops": count})
