"""Generate raw mesh arrays from RGBA; no rasterizer, GLB or texture baking."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import resource
import sys
import threading
from pathlib import Path
import subprocess
import time

import numpy as np
from PIL import Image


REVISIONS = {
    "trellis2": {"repo": "microsoft/TRELLIS.2-4B", "revision": "af44b45f2e35a493886929c6d786e563ec68364d", "license": "MIT"},
    "trellis": {"repo": "microsoft/TRELLIS-image-large", "revision": "25e0d31ffbebe4b5a97464dd851910efc3002d96", "license": "MIT"},
    "dinov3": {"repo": "facebook/dinov3-vitl16-pretrain-lvd1689m", "revision": "ea8dc2863c51be0a264bab82070e3e8836b02d51", "license": "DINOv3 License"},
    "dinov2": {"repo": "facebookresearch/dinov2", "revision": "85a24602099d397264d5b30461ad7f3bfd726ca1", "license": "Apache-2.0"},
}


def sample_voxel_attributes(vertices, coords, attrs, origin, voxel_size):
    """Sample nearest sparse voxel centers, including newly filled mesh holes.

    TRELLIS.2/FlexGEMM stores attributes at coords+0.5. Nearest sampling avoids
    treating absent sparse voxels as black, and requires no GPU/rasterizer.
    """
    from scipy.spatial import cKDTree

    vertices = np.asarray(vertices, dtype=np.float64)
    coords = np.asarray(coords, dtype=np.float64)
    attrs = np.asarray(attrs, dtype=np.float32)
    if vertices.ndim != 2 or vertices.shape[1] != 3:
        raise ValueError("vertices must have shape (N,3)")
    if coords.ndim != 2 or coords.shape[1] != 3 or not len(coords):
        raise ValueError("coords must have nonempty shape (K,3)")
    if attrs.ndim != 2 or len(attrs) != len(coords):
        raise ValueError("attrs must have shape (K,C)")
    if not np.isfinite(voxel_size) or voxel_size <= 0:
        raise ValueError("voxel_size must be positive")
    query = (vertices - np.asarray(origin, dtype=np.float64)) / voxel_size - 0.5
    _, indices = cKDTree(coords).query(query, workers=1)
    return attrs[indices].astype(np.float32, copy=False)


def srgb_to_linear(color):
    color = np.clip(np.asarray(color, dtype=np.float32), 0, 1)
    return np.where(color <= 0.04045, color / 12.92, ((color + 0.055) / 1.055) ** 2.4).astype(np.float32)


def save_raw(path: Path, vertices, faces, color, *, metallic=None, roughness=None):
    """Write the arrays consumed by the separate Blender task."""
    arrays = {"vertices": np.asarray(vertices, dtype=np.float32),
              "faces": np.asarray(faces, dtype=np.int32),
              "color": np.asarray(color, dtype=np.float32)}
    n = len(arrays["vertices"])
    if arrays["vertices"].shape != (n, 3) or not n:
        raise ValueError("vertices must have nonempty shape (N,3)")
    if arrays["faces"].ndim != 2 or arrays["faces"].shape[1] != 3 or not len(arrays["faces"]):
        raise ValueError("faces must have nonempty shape (M,3)")
    if arrays["faces"].min() < 0 or arrays["faces"].max() >= n:
        raise ValueError("face index outside vertices")
    if arrays["color"].shape != (n, 3):
        raise ValueError("color must have shape (N,3)")
    if (metallic is None) != (roughness is None):
        raise ValueError("metallic and roughness must be provided together")
    for name, value in (("metallic", metallic), ("roughness", roughness)):
        if value is not None:
            arrays[name] = np.asarray(value, dtype=np.float32)
            if arrays[name].shape != (n,):
                raise ValueError(f"{name} must have shape (N,)")
    for name, value in arrays.items():
        if not np.isfinite(value).all():
            raise ValueError(f"{name} contains nonfinite values")
        if name not in ("vertices", "faces") and (value.min() < 0 or value.max() > 1):
            raise ValueError(f"{name} must be in 0..1")
    np.savez_compressed(path, **arrays)
    return {"vertices": n, "faces": len(arrays["faces"])}


def read_rgba(path: Path):
    image = Image.open(path)
    if image.format != "PNG" or image.mode != "RGBA":
        raise ValueError("Input must be an RGBA PNG with an existing alpha channel")
    alpha = np.asarray(image.getchannel("A"))
    if np.all(alpha == 255) or not np.any(alpha > 204):
        raise ValueError("Input needs transparent background and opaque foreground; rembg is disabled")
    return image


def _nvidia_smi():
    result = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _git_revision(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _numpy(tensor):
    return tensor.detach().cpu().numpy()


class MemoryJournal:
    """Persist process/CUDA high-water marks while a model is still running."""

    def __init__(self, output, meta, torch):
        self.output, self.meta, self.torch = output, meta, torch
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.started = time.monotonic()
        self.attempt_started = None
        self.error = None
        self.cgroup = None
        try:
            for line in Path("/proc/self/cgroup").read_text().splitlines():
                if line.startswith("0::"):
                    self.cgroup = Path("/sys/fs/cgroup") / line[3:].lstrip("/")
                    break
        except OSError:
            pass
        meta.update(outcome="running", active_attempt=None,
                    rss_scope="process lifetime high-water mark; includes earlier attempts")
        self.sample()
        self.thread = threading.Thread(target=self._run, name="mesh3d-memory", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopped.wait(1):
            try:
                self.sample()
            except Exception as error:
                self.error = error
                print(f"Memory telemetry failed: {error}", file=sys.stderr, flush=True)
                return

    def sample(self):
        with self.lock:
            now = time.monotonic()
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            record = {"timestamp_unix": time.time(), "seconds": round(now - self.started, 3),
                      "peak_rss_mib": round(rss / (2**20 if sys.platform == "darwin" else 1024), 3)}
            cuda = self.torch.cuda
            for field, getter in (("allocated_mib", cuda.memory_allocated),
                                  ("reserved_mib", cuda.memory_reserved),
                                  ("peak_allocated_mib", cuda.max_memory_allocated),
                                  ("peak_reserved_mib", cuda.max_memory_reserved)):
                record[field] = round(getter() / 2**20, 3) if cuda.is_initialized() else 0
            if self.cgroup is not None:
                for name in ("current", "peak", "max", "swap.max"):
                    try:
                        value = (self.cgroup / f"memory.{name}").read_text().strip()
                        record[f"cgroup_memory_{name.replace('.', '_')}_bytes"] = int(value) if value != "max" else value
                    except OSError:
                        pass
                try:
                    record["cgroup_memory_oom_group"] = int((self.cgroup / "memory.oom.group").read_text())
                except OSError:
                    pass
                try:
                    record["cgroup_memory_events"] = {
                        key: int(value) for key, value in (line.split() for line in
                            (self.cgroup / "memory.events").read_text().splitlines())}
                except OSError:
                    pass
            index = self.meta["active_attempt"]
            record["active_attempt"] = index
            if index is not None:
                attempt = self.meta["attempts"][index]
                record.update(pipeline_type=attempt["pipeline_type"], stage=attempt["stage"])
                attempt.update(seconds=round(now - self.attempt_started, 3),
                               **{key: record[key] for key in
                                  ("peak_rss_mib", "peak_allocated_mib", "peak_reserved_mib")})
            self.meta.update(seconds=record["seconds"], memory=record)
            for key in ("peak_rss_mib", "peak_allocated_mib", "peak_reserved_mib"):
                self.meta[key] = max(self.meta.get(key, 0), record[key])
            with (self.output / "memory.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary = self.output / ".meta.json.tmp"
            with temporary.open("w") as stream:
                json.dump(self.meta, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.output / "meta.json")

    def begin_attempt(self, pipeline_type, seed):
        with self.lock:
            self.attempt_started = time.monotonic()
            attempt = {"pipeline_type": pipeline_type, "seed": seed,
                       "stage": "loading", "outcome": "running"}
            self.meta["active_attempt"] = len(self.meta["attempts"])
            self.meta["attempts"].append(attempt)
            self.sample()
            return attempt

    def update_attempt(self, **fields):
        with self.lock:
            self.meta["attempts"][self.meta["active_attempt"]].update(fields)
            self.sample()

    def finish_attempt(self):
        with self.lock:
            self.sample()
            self.meta["active_attempt"] = None
            self.sample()

    def stop(self):
        self.stopped.set()
        self.thread.join()
        if self.error is not None:
            self.meta["telemetry_error"] = f"{type(self.error).__name__}: {self.error}"


def generate(args):
    from adapter import assert_clean_imports, load_pipeline
    import torch

    root = args.root.expanduser().resolve()
    image = read_rgba(args.image)
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "raw.npz").exists() or (args.output / "meta.json").exists():
        raise FileExistsError(f"Variant already has output: {args.output}")
    repo = "TRELLIS.2" if args.model == "trellis2" else "TRELLIS"
    features = "dinov3" if args.model == "trellis2" else "dinov2"
    models = [dict(REVISIONS[args.model]), dict(REVISIONS[features])]
    models[0]["source_revision"] = _git_revision(root / "repos" / repo)
    dependencies = {}
    if args.model == "trellis2":
        models.append(dict(REVISIONS["trellis"], component="sparse_structure_decoder"))
        for name in ("CuMesh", "FlexGEMM"):
            dependencies[name] = {"revision": _git_revision(root / "repos" / name), "license": "MIT"}
        dependencies["o-voxel"] = {"revision": models[0]["source_revision"], "license": "MIT"}
    else:
        dependencies["FlexiCubes"] = {"license": "Apache-2.0", "revision": _git_revision(
            root / "repos/TRELLIS/trellis/representations/mesh/flexicubes")}
    if features == "dinov2":
        models[1]["revision"] = _git_revision(root / "repos/dinov2")
        models[1]["checkpoint_sha256"] = _sha256(root / "models/dinov2/dinov2_vitl14_reg4_pretrain.pth")
    config = root / "models" / ("trellis2" if args.model == "trellis2" else "trellis-v1") / "pipeline.json"
    config_data = json.loads(config.read_text())
    config_args = config_data["args"]
    sampler_keys = ("sparse_structure_sampler", "shape_slat_sampler", "tex_slat_sampler") if args.model == "trellis2" else ("sparse_structure_sampler", "slat_sampler")
    meta = {"model": args.model, "models": models, "dependencies": dependencies, "seed": args.seed,
            "input": {"filename": args.image.name, "sha256": hashlib.sha256(args.image.read_bytes()).hexdigest()},
            "parameters": {key: config_args[key] for key in sampler_keys},
            "axes": {"up": "+Z", "handedness": "right", "coordinates": "unchanged upstream XYZ"},
            "scale": {"canonical_bounds": [-0.5, 0.5], "units": "normalized object; no physical calibration"},
            "color_space": "linear RGB; IEC sRGB transfer decoded from normalized decoder RGB",
            "attribute_sampling": "nearest sparse voxel center (coords+0.5); CPU cKDTree" if args.model == "trellis2" else "mesh decoder vertex_attrs[:, :3]",
            "pipeline_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            "torch_version": torch.__version__, "cuda_version": torch.version.cuda,
            "nvidia_smi_before": _nvidia_smi(), "attempts": []}
    if not torch.cuda.is_available():
        raise RuntimeError("Generation requires CUDA on dev-ubuntu")
    requested_type = args.pipeline_type
    pipeline_types = [None]
    if args.model == "trellis2":
        pipeline_types = ["1024_cascade", "512"] if requested_type == "auto" else [requested_type]
    meta["parameters"].update({"low_vram": args.model == "trellis2", "num_samples": 1,
                               "formats": ["mesh"], "preprocess_image": True})
    if args.model == "trellis2":
        meta["parameters"].update(pipeline_type=pipeline_types[0],
                                  oom_fallback="512" if requested_type == "auto" else None,
                                  max_num_tokens=49152)
    telemetry = MemoryJournal(args.output, meta, torch)
    failure = None
    try:
        for pipeline_type in pipeline_types:
            pipeline = None
            result = None
            with telemetry.lock:
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats()
                attempt = telemetry.begin_attempt(pipeline_type, args.seed)
            try:
                pipeline = load_pipeline(root, args.model, pipeline_type=pipeline_type)
                pipeline.cuda()
                telemetry.update_attempt(stage="inference")
                if args.model == "trellis2":
                    result = pipeline.run(image, seed=args.seed, pipeline_type=pipeline_type,
                                          max_num_tokens=49152)[0]
                    vertices, faces = _numpy(result.vertices), _numpy(result.faces)
                    attrs = sample_voxel_attributes(vertices, _numpy(result.coords), _numpy(result.attrs),
                                                     _numpy(result.origin), result.voxel_size)
                    fields = {"metallic": np.clip(attrs[:, 3], 0, 1),
                              "roughness": np.clip(attrs[:, 4], 0, 1)}
                    color = srgb_to_linear(attrs[:, :3])
                else:
                    result = pipeline.run(image, seed=args.seed, formats=["mesh"])["mesh"][0]
                    vertices, faces = _numpy(result.vertices), _numpy(result.faces)
                    color = srgb_to_linear(_numpy(result.vertex_attrs)[:, :3])
                    fields = {}
                telemetry.update_attempt(stage="saving")
                mesh = save_raw(args.output / "raw.npz", vertices, faces, color, **fields)
                with telemetry.lock:
                    meta["mesh"] = mesh
                assert_clean_imports()
                torch.cuda.synchronize()
                telemetry.update_attempt(outcome="ok", stage="finished")
            except torch.OutOfMemoryError as error:
                telemetry.update_attempt(outcome="oom", stage="finished", error=str(error))
                if requested_type != "auto" or pipeline_type != "1024_cascade":
                    failure = error
                error.__traceback__ = None
            except Exception as error:
                telemetry.update_attempt(outcome="error", stage="finished", error=f"{type(error).__name__}: {error}")
                failure = error
                error.__traceback__ = None
            finally:
                telemetry.finish_attempt()
                del result, pipeline
                gc.collect()
                torch.cuda.empty_cache()
            if attempt["outcome"] == "ok" or failure is not None:
                break
    finally:
        telemetry.stop()
        meta["outcome"] = meta["attempts"][-1]["outcome"] if meta["attempts"] else "error"
        try:
            meta["nvidia_smi_after"] = _nvidia_smi()
        finally:
            telemetry.sample()
    if failure is not None:
        raise failure
    print(json.dumps({"model": args.model, "output": str(args.output), "seconds": meta["seconds"],
                      "peak_allocated_mib": meta["peak_allocated_mib"], "peak_reserved_mib": meta["peak_reserved_mib"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", choices=["trellis2", "trellis"], required=True)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pipeline-type", choices=["auto", "512", "1024_cascade"], default="auto")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    if args.image is None or args.output is None:
        parser.error("generation requires --image and --output")
    if args.model == "trellis" and args.pipeline_type != "auto":
        parser.error("--pipeline-type applies only to trellis2")
    generate(args)


if __name__ == "__main__":
    main()
