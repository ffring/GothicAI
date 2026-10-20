"""Render every raw mesh triangle on CPU, with unlit vertex color and Z up."""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
import numpy as np


def render(source: Path, output: Path):
    with np.load(source, allow_pickle=False) as raw:
        vertices = raw["vertices"]
        faces = raw["faces"]
        color = raw["color"]

    # The same orthographic camera is used for both upstream Z-up meshes.
    camera = np.array([1.5, -2.0, 1.2], dtype=np.float32)
    camera /= np.linalg.norm(camera)
    right = np.cross([0.0, 0.0, 1.0], camera)
    right /= np.linalg.norm(right)
    up = np.cross(camera, right)
    centered = vertices - (vertices.min(axis=0) + vertices.max(axis=0)) / 2
    projected = centered @ np.stack([right, up], axis=1)
    # Painter's order preserves overlapping front surfaces without a GPU.
    order = np.argsort((centered @ camera)[faces].mean(axis=1), kind="stable")
    face_color = np.clip(color[faces].mean(axis=1), 0, 1)[order]
    face_color = np.where(face_color <= 0.0031308, 12.92 * face_color,
                          1.055 * face_color ** (1 / 2.4) - 0.055)
    collection = PolyCollection(projected[faces[order]], facecolors=face_color,
                                edgecolors="none", antialiaseds=False)
    fig, ax = plt.subplots(figsize=(7, 7), dpi=160, facecolor="#e8e5df")
    ax.set_facecolor("#e8e5df")
    ax.add_collection(collection)
    bounds = np.max(np.abs(projected), axis=0)
    radius = float(bounds.max()) * 1.08
    ax.set(xlim=(-radius, radius), ylim=(-radius, radius), aspect="equal")
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"{output}: {len(vertices)} vertices, {len(faces)} triangles; CPU, all faces")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(args.source, args.output)
