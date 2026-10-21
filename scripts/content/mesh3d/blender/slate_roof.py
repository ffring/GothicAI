"""Blender scene of the slate roof NW_CITY_ROOF_04 and its passes, seen a little from below the slope.

Run by scripts/content/slate_bake.py render:

    Blender -b --factory-startup -P slate_roof.py -- LAYOUT.json OUTDIR RESOLUTION SAMPLES [sun=E] STONE.png...

sun=0 bakes no sun: the light pass is the sky over the pitched roof only, which does not depend on
where the game's sun stands (the albedo then carries occlusion, not a light direction).

Every slate of the layout becomes a solid set on its own plane: a bowed, slightly wavy face with
rounded hand-dressed edges, and side walls down to its flat underside. Copies across the square's
edges make the render tile. An orthographic camera looks at one 6.6 m square up the slope, tilted
by TILT from straight down, the way the roof is seen from the street: the thick tails and dressed
edges show. The render is cos(TILT) as tall as wide and still covers exactly one period of the
slope, so it tiles once stretched back to a square. OUTDIR/passes.exr holds the stone colour (DiffCol), ambient occlusion at
two distances (AO, crevice), the shading normal, the position (height in Z), the slate id
(IndexOB, slate id + 1), the light of sky and sun without colour (DiffDir + DiffInd) and the lit
roof (Combined).
"""
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector, geometry

CREVICE_DISTANCE = .025   # metres: the AOV "crevice" sees only the narrow gaps
AO_DISTANCE = .15         # metres: the AO pass sees the steps at the course lines and joints
PITCH = math.radians(45)  # roof pitch: tilts the sky towards the ridge
GROUND = .08              # light from below the true horizon
SUN_ELEVATION = 32.       # degrees above the roof plane, from the ridge side
SUN_FROM_LEFT = 35.       # degrees the sun swings to the left of the ridge direction
SUN_ENERGY = 2.5
TILT = math.radians(25)   # camera tilt from straight down, looking up the slope

argv = sys.argv[sys.argv.index('--') + 1:]
spec = json.loads(Path(argv[0]).read_text())
out_dir = Path(argv[1])
resolution, samples = int(argv[2]), int(argv[3])
options = dict(a.split('=', 1) for a in argv[4:] if '=' in a)   # sun=0: sky only, no baked sun
stones = [Path(p) for p in argv[4:] if '=' not in p]
period, stone_metres = spec['period'], spec['stone_metres']

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
try:
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type = 'METAL'
    prefs.refresh_devices()
    for device in prefs.devices:
        device.use = True
    scene.cycles.device = 'GPU'
except Exception as error:   # no GPU backend: the CPU renders the same passes
    print('slate_roof: CPU render,', error)
    scene.cycles.device = 'CPU'
scene.cycles.samples = samples
scene.cycles.use_adaptive_sampling = False
scene.cycles.use_denoising = False
scene.render.resolution_x = resolution
scene.render.resolution_y = round(resolution * math.cos(TILT))
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = 'Standard'

# Sky over a pitched roof: in the roof's own frame the zenith leans towards the ridge (+Y) by the
# pitch, and below the true horizon there is only dim ground. Light from the ridge side is what makes
# each course edge shade the course below it, as in the original.
world = bpy.data.worlds.new('sky')
scene.world = world
world.use_nodes = True
wn, wl = world.node_tree.nodes, world.node_tree.links
coords = wn.new('ShaderNodeTexCoord')
zenith = wn.new('ShaderNodeVectorMath')
zenith.operation = 'DOT_PRODUCT'
zenith.inputs[1].default_value = (0, math.sin(PITCH), math.cos(PITCH))
wl.new(coords.outputs['Generated'], zenith.inputs[0])
sky = wn.new('ShaderNodeMath')
sky.operation, sky.use_clamp = 'MAXIMUM', False
sky.inputs[1].default_value = GROUND
wl.new(zenith.outputs['Value'], sky.inputs[0])
wl.new(sky.outputs['Value'], wn['Background'].inputs['Strength'])
wn['Background'].inputs['Color'].default_value = (1, 1, 1, 1)
world.light_settings.distance = AO_DISTANCE

layer = scene.view_layers[0]
layer.use_pass_diffuse_color = True
layer.use_pass_ambient_occlusion = True
layer.use_pass_normal = True
layer.use_pass_position = True
layer.use_pass_object_index = True
layer.use_pass_diffuse_direct = True    # sky light without the stone colour
layer.use_pass_diffuse_indirect = True
aov = layer.aovs.add()
aov.name, aov.type = 'crevice', 'VALUE'


def node(nodes, kind, **props):
    n = nodes.new(kind)
    for key, value in props.items():
        setattr(n, key, value)
    return n


def stone_material(index, path):
    mat = bpy.data.materials.new(f'stone{index}')
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    tex = node(nodes, 'ShaderNodeTexImage', interpolation='Cubic', extension='REPEAT')
    tex.image = bpy.data.images.load(str(path))
    tex.image.colorspace_settings.name = 'sRGB'
    bsdf = node(nodes, 'ShaderNodeBsdfDiffuse')
    out = node(nodes, 'ShaderNodeOutputMaterial')
    links.new(tex.outputs['Color'], bsdf.inputs['Color'])
    links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    ao = node(nodes, 'ShaderNodeAmbientOcclusion', samples=16, only_local=False)
    ao.inputs['Distance'].default_value = CREVICE_DISTANCE
    aov_out = node(nodes, 'ShaderNodeOutputAOV', aov_name='crevice')
    links.new(ao.outputs['AO'], aov_out.inputs['Value'])
    return mat


materials = [stone_material(i, p) for i, p in enumerate(stones)]

params = spec['parameters']


def face_height(slate, xy):
    """Height of the top face: the slate's plane and thickness, a bow across its width, cleavage
    waviness, and hand-dressed edges that round off towards the outline. The hidden head stays flat."""
    a, bx, by = slate['plane']
    face = slate['face']
    x, y = xy[:, 0], xy[:, 1]
    y_bottom, height = slate['y_bottom'], slate['height']
    x0, slant, width = face['lateral']
    v = 1 - (y - y_bottom) / height
    across = np.clip((x - x0 - slant * height * v) / width, 0, 1)
    seen = np.clip((y_bottom + 1.3 * height - y) / (.3 * height), 0, 1)
    bow = face['bow'] * (1 - (2 * across - 1) ** 2) * seen
    along = x * math.cos(face['wave_angle']) + y * math.sin(face['wave_angle'])
    wave = face['wave'] * np.sin(2 * math.pi * along / face['wave_length'] + face['wave_phase']) * seen
    ring = np.asarray(slate['outline'])
    start, step = ring, np.roll(ring, -1, axis=0) - ring
    t = np.clip(((xy[:, None, :] - start[None]) * step[None]).sum(-1) / ((step ** 2).sum(-1)[None] + 1e-12), 0, 1)
    nearest = start[None] + t[..., None] * step[None]
    edge = np.sqrt(((xy[:, None, :] - nearest) ** 2).sum(-1)).min(axis=1)
    rounded = params['round_drop'] * slate['thickness'] * np.clip(1 - edge / params['round'], 0, 1) ** 2
    return a + bx * x + by * y + slate['thickness'] + bow + wave - rounded


for slate in spec['slates']:
    a, bx, by = slate['plane']
    ou, ov = slate['uv_offset']
    ring = slate['outline']
    n = len(ring)
    flat = [Vector((x, y)) for x, y in ring + slate['points']]
    coords, _edges, faces, origin, _oe, _of = geometry.delaunay_2d_cdt(flat, [], [list(range(n))], 1, 1e-7)
    xy = np.array([[c.x, c.y] for c in coords])
    z = face_height(slate, xy)
    bm = bmesh.new()
    top = [bm.verts.new((x, y, h)) for (x, y), h in zip(xy, z)]
    for face in faces:
        f = bm.faces.new([top[i] for i in face])
        f.smooth = True
    bm.normal_update()
    for f in bm.faces:
        if f.normal.z < 0:
            f.normal_flip()
    ring_top = {}
    for out_index, sources in enumerate(origin):
        for source in sources:
            if source < n:
                ring_top[source] = top[out_index]
    bottom = [bm.verts.new((x, y, a + bx * x + by * y)) for x, y in ring]
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((bottom[i], bottom[j], ring_top[j], ring_top[i]))
    uv = bm.loops.layers.uv.new('UVMap')
    for face in bm.faces:
        for loop in face.loops:
            loop[uv].uv = (loop.vert.co.x / stone_metres + ou, loop.vert.co.y / stone_metres + ov)
    mesh = bpy.data.meshes.new(f'slate{slate["id"]}')
    bm.to_mesh(mesh)
    bm.free()
    mesh.validate()
    mesh.materials.append(materials[slate['stone'] % len(materials)])
    for dx, dy in slate['copies']:
        obj = bpy.data.objects.new(f'slate{slate["id"]}_{dx:+.1f}_{dy:+.1f}', mesh)
        obj.location = (dx, dy, 0)
        obj.pass_index = slate['id'] + 1
        scene.collection.objects.link(obj)

# Sun over the ridge, a little from the left, as in the original: each slate gets light and shade
# across its bowed face, and every course edge throws a shadow on the course below.
sun = bpy.data.lights.new('sun', 'SUN')
sun.energy, sun.angle = float(options.get('sun', SUN_ENERGY)), math.radians(8)
sun_obj = bpy.data.objects.new('sun', sun)
sun_obj.rotation_euler = (math.radians(SUN_ELEVATION - 90), 0, math.radians(SUN_FROM_LEFT))
scene.collection.objects.link(sun_obj)

camera = bpy.data.cameras.new('top')
camera.type, camera.ortho_scale = 'ORTHO', period
camera.clip_start, camera.clip_end = .01, 10
cam = bpy.data.objects.new('top', camera)
cam.location = (period / 2, period / 2 - 3 * math.sin(TILT), .05 + 3 * math.cos(TILT))
cam.rotation_euler = (TILT, 0, 0)
scene.collection.objects.link(cam)
scene.camera = cam

scene.render.image_settings.media_type = 'MULTI_LAYER_IMAGE'   # Blender 5: multilayer EXR
scene.render.image_settings.file_format = 'OPEN_EXR_MULTILAYER'
scene.render.image_settings.color_depth = '32'
scene.render.filepath = str(out_dir / 'passes.exr')
bpy.ops.render.render(write_still=True)
print('slate_roof: wrote', out_dir / 'passes.exr')
