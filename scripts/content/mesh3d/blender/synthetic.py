"""One CPU integration run: two dense meshes, two controls, GLB checks and sheets."""
import argparse
import json
from pathlib import Path
import struct
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry import fit_bbox, triangle_areas
from postprocess import process_arrays, mesh_arrays, read_raw


def barrel():
    sides, rings = 512, 289
    theta, y = np.meshgrid(np.arange(sides) * (2 * np.pi / sides), np.linspace(0, 1, rings))
    radius = 0.36 + 0.075 * np.sin(np.pi * y)
    radius += 0.0007 * np.sin(theta * 19 + y * 13) * np.sin(np.pi * y)
    v = np.stack((radius * np.cos(theta), y, radius * np.sin(theta)), axis=-1).reshape(-1, 3)
    cells = (np.arange(rings - 1)[:, None] * sides + np.arange(sides)).ravel()
    next_cell = (cells // sides) * sides + (cells + 1) % sides
    f = np.concatenate((np.column_stack((cells, cells + sides, next_cell)),
                        np.column_stack((next_cell, cells + sides, next_cell + sides))))
    # Fan caps, no coincident duplicate vertices.
    bottom, top = len(v), len(v) + 1
    v = np.vstack((v, [0, 0, 0], [0, 1, 0]))
    indices = np.arange(sides)
    f = np.vstack((f, np.column_stack((np.full(sides, bottom), indices, (indices + 1) % sides)),
                   np.column_stack((np.full(sides, top), (indices + 1) % sides + (rings - 1) * sides,
                                    indices + (rings - 1) * sides))))
    metal = (np.min(abs(v[:, 1, None] - np.array([0.2, 0.5, 0.8])), axis=1) < 0.027).astype(float)
    color = np.tile([0.24, 0.095, 0.026], (len(v), 1))
    color *= (0.86 + 0.25 * (0.5 + 0.5 * np.sin(np.arctan2(v[:, 2], v[:, 0]) * 16)))[:, None]
    color[metal > 0.5] = [0.035, 0.038, 0.04]
    return v, f.astype(np.int32), color, metal


def crate():
    divisions = 160
    u, w = np.meshgrid(np.linspace(-0.5, 0.5, divisions + 1),
                       np.linspace(-0.5, 0.5, divisions + 1))
    vertices, faces = [], []
    for axis in range(3):
        for sign in (-1, 1):
            coords = np.zeros((u.size, 3))
            other = [i for i in range(3) if i != axis]
            coords[:, other[0]], coords[:, other[1]] = u.ravel(), w.ravel()
            coords[:, axis] = sign * (0.5 + 0.0005 * np.sin(u.ravel() * 39)
                                      * np.sin(w.ravel() * 23)
                                      * np.cos(u.ravel() * np.pi) * np.cos(w.ravel() * np.pi))
            offset = sum(len(part) for part in vertices)
            cells = (np.arange(divisions)[:, None] * (divisions + 1) + np.arange(divisions)).ravel()
            local = np.vstack((np.column_stack((cells, cells + 1, cells + divisions + 1)),
                               np.column_stack((cells + 1, cells + divisions + 2, cells + divisions + 1))))
            vertices.append(coords)
            faces.append(local + offset)
    v, f = np.vstack(vertices), np.vstack(faces).astype(np.int32)
    metal = (np.sum(abs(v) > 0.405, axis=1) >= 3).astype(float)
    shade = 0.85 + 0.12 * (np.floor((v[:, 1] + 0.5) * 6) % 3)
    color = np.array([0.28, 0.12, 0.035])[None, :] * shade[:, None]
    color[metal > 0.5] = [0.035, 0.038, 0.04]
    return v, f, color, metal


def target(kind):
    return {"id": f"synthetic-{kind}", "visual": f"SYNTHETIC_{kind.upper()}", "kind": kind,
            "bbox_min": [-0.47, -0.08, -0.38], "bbox_max": [0.47, 1.12, 0.38],
            "budget_tris": 1800 if kind == "barrel" else 1200,
            "zones": [{"key": "PILOT_WOOD", "group": "WOOD", "role": "boards"},
                      {"key": "PILOT_IRON", "group": "METAL", "role": "hoops and corners"}]}


def glb_document(path):
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise AssertionError(f"invalid GLB header: {path}")
    size, kind = struct.unpack_from("<II", data, 12)
    if kind != 0x4E4F534A:
        raise AssertionError("first GLB chunk must be JSON")
    return json.loads(data[20:20 + size])


def verify(obj, raw_v, raw_f, raw_m, meta, goal, metrics, output):
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    v, f = mesh_arrays(obj)
    aligned = fit_bbox(raw_v, meta, goal)
    # Nearest final triangle sampled over the entire dense source surface.
    coords = v[:, [0, 2, 1]].copy()
    coords[:, 1] *= -1
    tree = BVHTree.FromPolygons(coords.tolist(), f.tolist(), all_triangles=True)
    centers = aligned[raw_f].mean(axis=1)[:, [0, 2, 1]]
    centers[:, 1] *= -1
    indices = np.fromiter((tree.find_nearest(Vector(point))[2] for point in centers),
                          dtype=np.int32, count=len(centers))
    pred = np.array([p.material_index == 1 for p in obj.data.polygons])[indices]
    expected = raw_m[raw_f].mean(axis=1) > 0.5
    area = triangle_areas(aligned, raw_f)
    iou = float(area[pred & expected].sum() / area[pred | expected].sum())
    accuracy = float(area[pred == expected].sum() / area.sum())
    # UV singular values measure physical checker lengths, including anisotropy.
    uv = np.array([item.uv[:] for item in obj.data.uv_layers.active.data]).reshape(-1, 3, 2)
    edges = v[f[:, 1:]] - v[f[:, :1]]
    e1 = edges[:, 0] / np.linalg.norm(edges[:, 0], axis=1)[:, None]
    normals = np.cross(edges[:, 0], edges[:, 1])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    e2 = np.cross(normals, e1)
    local = np.stack((np.einsum("fij,fj->fi", edges, e1),
                      np.einsum("fij,fj->fi", edges, e2)), axis=-1)
    jacobian = np.linalg.solve(local, uv[:, 1:] - uv[:, :1])
    scale = np.linalg.svd(jacobian, compute_uv=False)
    final_area = triangle_areas(v, f)
    # Area-weighted average in each principal direction avoids tiny bevel outliers.
    mean_scale = np.average(scale, weights=final_area, axis=0)
    doc = glb_document(output / "model.glb")
    assert {m["name"] for m in doc["materials"]} == {z["key"] for z in goal["zones"]}
    assert {m["extras"]["group"] for m in doc["materials"]} == {"WOOD", "METAL"}
    assert np.allclose(v.min(axis=0), goal["bbox_min"], atol=1e-5)
    assert np.allclose(v.max(axis=0), goal["bbox_max"], atol=1e-5)
    assert metrics["triangles"] <= goal["budget_tris"], metrics
    assert metrics["open_edges"] == metrics["non_manifold_edges"] == 0, metrics
    assert iou >= 0.9, f"iron area IoU {iou:.4f} < 0.9"
    assert np.all(abs(mean_scale - 1) <= 0.05), f"UV scale {mean_scale} outside 1m +/-5%"
    return {"triangles": metrics["triangles"], "raw_triangles": len(raw_f),
            "metal_area_iou": iou, "zone_area_accuracy": accuracy,
            "uv_meters_scale": mean_scale.tolist(), "budget": goal["budget_tris"]}


def main():
    import parametric
    import sheet
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True)
    args = p.parse_args(sys.argv[sys.argv.index("--") + 1:])
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = {}
    for kind, generate in (("barrel", barrel), ("crate", crate)):
        goal = target(kind)
        item = output / kind
        item.mkdir(parents=True, exist_ok=True)
        (item / "target.json").write_text(json.dumps(goal, indent=2) + "\n")
        v, f, c, m = generate()
        # Exercise non-Y up, nonmeter source, yaw and non-origin target anchor.
        angle = np.radians(23)
        rotation = np.array([[np.cos(angle), 0, -np.sin(angle)], [0, 1, 0],
                             [np.sin(angle), 0, np.cos(angle)]])
        v = (v @ rotation)[:, [0, 2, 1]] * 100
        v[:, 1] *= -1
        meta = {"up_axis": "+Z", "meters_per_unit": 0.01}
        raw_path, meta_path = item / "raw.npz", item / "meta.json"
        np.savez_compressed(raw_path, vertices=v.astype(np.float32), faces=f,
                            color=c.astype(np.float32), metallic=m.astype(np.float32),
                            roughness=np.full(len(v), 0.7, dtype=np.float32))
        meta_path.write_text(json.dumps(meta) + "\n")
        v, f, c, m, _ = read_raw(raw_path)
        obj, metrics = process_arrays(v, f, c, m, goal, item / "processed", meta)
        summary[kind] = verify(obj, v, f, m, meta, goal, metrics, item / "processed")
        pv, pf, pc, pm = parametric.build(kind)
        control = item / "parametric"
        control.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(control / "raw.npz", vertices=pv, faces=pf, color=pc, metallic=pm)
        (control / "meta.json").write_text(json.dumps({"up_axis": "+Y", "meters_per_unit": 1.0}) + "\n")
        process_arrays(pv, pf, pc, pm, goal, item / "parametric",
                       {"up_axis": "+Y", "meters_per_unit": 1.0}, cleanup=False)
        variants = {"title": f"Synthetic {kind}", "variants": [
            {"label": "synthetic x analytic x seed 0", "raw": str(raw_path), "meta": str(meta_path),
             "result": str(item / "processed")},
            {"label": "parametric control", "result": str(item / "parametric")}]}
        variant_path = item / "variants.json"
        variant_path.write_text(json.dumps(variants, indent=2) + "\n")
        # Same public sheet entry point, retaining Blender's command-line convention.
        saved_argv = sys.argv
        try:
            sys.argv = ["sheet.py", "--", "--variants", str(variant_path), "--out", str(item / "sheet.png")]
            sheet.main()
        finally:
            sys.argv = saved_argv
        assert (item / "sheet.png").stat().st_size > 10000
    (output / "checks.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
