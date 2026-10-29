#!/usr/bin/env python3
"""Generate a procedural cube in empty test content, optionally at the NEWWORLD start."""

import argparse
import json
from pathlib import Path
import struct


VISUAL = "VOB_LAYER_TEST_CUBE"
POSITION_CM = [30213, 5186, -15710]


def cube_glb():
    """Two metre cube, with its origin at the centre of its bottom face."""
    positions, normals, uvs, indices = [], [], [], []
    for normal, corners in (
        ((1, 0, 0), ((1, 0, -1), (1, 2, -1), (1, 2, 1), (1, 0, 1))),
        ((-1, 0, 0), ((-1, 0, 1), (-1, 2, 1), (-1, 2, -1), (-1, 0, -1))),
        ((0, 1, 0), ((-1, 2, -1), (-1, 2, 1), (1, 2, 1), (1, 2, -1))),
        ((0, -1, 0), ((-1, 0, 1), (-1, 0, -1), (1, 0, -1), (1, 0, 1))),
        ((0, 0, 1), ((-1, 0, 1), (1, 0, 1), (1, 2, 1), (-1, 2, 1))),
        ((0, 0, -1), ((1, 0, -1), (-1, 0, -1), (-1, 2, -1), (1, 2, -1))),
    ):
        first = len(positions)
        positions.extend(corners)
        normals.extend([normal] * 4)
        uvs.extend(((0, 0), (0, 1), (1, 1), (1, 0)))
        indices.extend((first, first + 1, first + 2, first, first + 2, first + 3))

    binary = bytearray()
    views, accessors = [], []
    for rows, components, code, target in (
        (positions, 3, 5126, 34962), (normals, 3, 5126, 34962),
        (uvs, 2, 5126, 34962), ([(i,) for i in indices], 1, 5123, 34963),
    ):
        offset = len(binary)
        for row in rows:
            binary.extend(struct.pack("<" + ("f" if code == 5126 else "H") * components, *row))
        views.append({"buffer": 0, "byteOffset": offset,
                      "byteLength": len(binary) - offset, "target": target})
        accessors.append({"bufferView": len(views) - 1, "componentType": code,
                          "count": len(rows), "type": "SCALAR" if components == 1 else f"VEC{components}",
                          "min": [min(row[i] for row in rows) for i in range(components)],
                          "max": [max(row[i] for row in rows) for i in range(components)]})
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}], "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
            "indices": 3, "material": 0}]}],
        "materials": [{"name": "VOB_LAYER_TEST", "extras": {"group": "STONE"}}],
        "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors,
    }
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    return (struct.pack("<4sII", b"glTF", 2, 28 + len(text) + len(binary))
            + struct.pack("<I4s", len(text), b"JSON") + text
            + struct.pack("<I4s", len(binary), b"BIN\0") + binary)


def make_content(directory, collision=None):
    directory = directory.expanduser().resolve()
    working = (Path.home() / ".local/share/gothic-ai/content").resolve()
    if directory == working or working in directory.parents:
        raise ValueError("refusing to write into working content")
    if directory.exists() and next(directory.iterdir(), None) is not None:
        raise ValueError("test content directory must be new or empty")
    meshes = directory / "meshes"
    meshes.mkdir(parents=True)
    (meshes / "cube.glb").write_bytes(cube_glb())
    (meshes / "meshes.json").write_text(json.dumps({VISUAL: {"glb": "cube.glb"}}) + "\n")
    if collision:
        layers = directory / "worlds/NEWWORLD/vobs.d"
        layers.mkdir(parents=True)
        (layers / "cube.json").write_text(json.dumps({"format": 1, "add": [{
            "key": "test.cube", "kind": "mesh", "visual": VISUAL,
            "position": POSITION_CM, "yaw_deg": 0, "collision": collision,
        }]}) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", required=True, type=Path)
    parser.add_argument("--collision", choices=("mesh", "none"))
    args = parser.parse_args()
    make_content(args.content, args.collision)


if __name__ == "__main__":
    main()
