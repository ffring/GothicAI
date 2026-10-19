#!/usr/bin/env python3
"""Text-only material bakeoff. GPU stages must run on dev-ubuntu, never on Mac.

prepare resolves public HF metadata without accepting gates. download fetches only
needed pinned components. GPU stages are serialized by run-materials.sh, which
restores any temporarily stopped services. All binary output is outside git.
"""
from __future__ import annotations
import argparse
import gc
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

MODELS = {
    'flux': ('black-forest-labs/FLUX.1-schnell', 'Apache-2.0'),
    'sdxl': ('stabilityai/stable-diffusion-xl-base-1.0', 'CreativeML OpenRAIL++-M'),
    'stablematerials': ('gvecchio/StableMaterials', 'openrail'),
    'normals': ('prs-eth/marigold-normals-v1-1', 'CreativeML OpenRAIL++-M'),
    'appearance': ('prs-eth/marigold-iid-appearance-v1-1', 'CreativeML OpenRAIL++-M'),
}
MATERIALS = [
    ('cobblestone', 'Irregular rounded grey cobblestone pavement, small stones, dirty recessed joints', .83),
    ('stone-wall', 'City wall masonry, staggered rough grey limestone blocks, recessed lime mortar', .85),
    ('plaster', 'Weathered pale ochre lime plaster for a timber framed house, fine cracks and stains', .91),
    ('floorboards', 'Wide parallel oak floorboards, worn grain, narrow dark gaps, old nail holes', .76),
    ('timber', 'Rough hewn parallel timber beams and logs, dark aged wood grain, deep checks', .86),
    ('thatch', 'Dense overlapping golden brown thatched roof straw, irregular coarse dry fibres', .94),
    ('roof-tiles', 'Rows of overlapping curved terracotta roof tiles, chipped edges, faded red clay', .84),
    ('mud', 'Compacted brown earth and dried mud, shallow footprints, small pebbles and damp patches', .90),
    ('forest-floor', 'Forest floor, dark leaf litter and soil, scattered moss and short sparse grass', .94),
    ('rock', 'Rough grey rock face, fractured layered stone, mineral grain and traces of lichen', .89),
]
STYLE = ', medieval, worn and dirty, slightly muted colors, diffuse light without highlights or shadows, strictly top-down orthographic view, seamless tile, no text or logos'
NEGATIVE = 'text, logos, watermark, perspective, horizon, cast shadows, specular highlights, objects, frame'
PIPELINES = ['flux-marigold', 'flux-filter', 'sdxl-marigold', 'sdxl-filter', 'stablematerials']
MAPS = ('albedo', 'normal', 'roughness', 'metallic', 'height', 'ao')


def read_registry(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_registry(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n' for r in records))
    temporary.replace(path)


def prepare(path):
    metadata = {}
    for key, (model, license_name) in MODELS.items():
        with urllib.request.urlopen('https://huggingface.co/api/models/' + model, timeout=30) as response:
            info = json.load(response)
        metadata[key] = {'id': model, 'revision': info['sha'], 'license': license_name, 'gated': info.get('gated', False)}
    records = []
    for index, (material, description, roughness) in enumerate(MATERIALS):
        for pipeline in PIPELINES:
            base = pipeline.split('-')[0]
            keys = [base] + (['normals', 'appearance'] if pipeline.endswith('marigold') else [])
            models = [{k: v for k, v in metadata[key].items() if k != 'gated'} for key in keys]
            gated = [metadata[key]['id'] for key in keys if metadata[key]['gated']]
            record = {
                'id': f'bakeoff-{material}-{pipeline}', 'kind': 'material', 'replaces': [],
                'pipeline': pipeline, 'models': models, 'prompt': description + STYLE,
                'negative': NEGATIVE, 'seed': 31000 + index, 'status': 'draft', 'files': {},
                'parameters': {'material': material, 'resolution': 512 if base == 'stablematerials' else 1024,
                    'steps': 50 if base == 'stablematerials' else (4 if base == 'flux' else 30),
                    'guidance_scale': 10.0 if base == 'stablematerials' else (0.0 if base == 'flux' else 7.0),
                    'roughness_base': roughness, 'tileable': True,
                    'tiling': 'native feature rolling and padded decode' if base == 'stablematerials' else 'circular Conv2d padding in UNet and VAE, including asymmetric downsample padding',
                    'height_filter': {'sigma_small_px_at_1024': 2, 'sigma_large_px_at_1024': 32, 'normal_strength': .025},
                    'input': 'text only; map estimators see only the generated source image'},
                'generation': {'outcome': 'pending'},
            }
            if pipeline.endswith('marigold'):
                record['parameters']['marigold'] = {'steps': 4, 'ensemble_size': 1, 'processing_resolution': 1024, 'match_input_resolution': True, 'seed': 41000 + index,
                    'tiling': 'circular UNet/VAE convolution and asymmetric downsample padding; generated 1024 source input',
                    'height_ao': 'non-neural luminance bandpass from generated source, shared with filter variant'}
            if base == 'flux':
                record['parameters']['quantization'] = 'int8 transformer and T5 (planned; gated model skipped)'
            if gated:
                record['skip_reason'] = 'HF gated agreement required; not accepted: ' + ', '.join(gated)
                record['generation'] = {'outcome': 'skipped'}
            records.append(record)
    write_registry(path, records)
    print(f'{len(records)} recipes; {sum(bool(r.get("skip_reason")) for r in records)} skipped')


def download(records, root):
    from huggingface_hub import snapshot_download
    models = {m['id']: m for r in records if not r.get('skip_reason') for m in r['models']}
    for model in models.values():
        if sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) > 60_000_000_000:
            raise RuntimeError('60 GB pre-download safety reserve reached (80 GB task limit)')
        stable = model['id'] == MODELS['stablematerials'][0]
        allowed = ['model_index.json', 'README.md', 'LICENSE*', 'pipeline.py', 'scheduler/*', 'tokenizer/*', 'tokenizer_2/*',
                   'text_encoder/*.json', 'text_encoder_2/*.json', 'unet/config.json', 'vae/config.json']
        for component in ['text_encoder', 'unet', 'vae'] + ([] if stable else ['text_encoder_2']):
            allowed.append(component + ('/*.safetensors' if stable else '/*fp16.safetensors'))
        snapshot = snapshot_download(model['id'], revision=model['revision'], allow_patterns=allowed, cache_dir=root/'hf/hub', token=False, max_workers=4)
        print(model['id'], snapshot, flush=True)


def model_path(records, root, key):
    model = next(m for r in records for m in r['models'] if m['id'] == MODELS[key][0])
    return root / 'hf/hub' / ('models--' + model['id'].replace('/', '--')) / 'snapshots' / model['revision']


def circular(module):
    import torch
    import types
    from diffusers.models.downsampling import Downsample2D
    count = 0
    for layer in module.modules():
        if isinstance(layer, torch.nn.Conv2d):
            layer.padding_mode = 'circular'
            count += 1
        if isinstance(layer, Downsample2D) and layer.use_conv and layer.padding == 0:
            original = layer.forward
            def forward(self, hidden_states, *args, _original=original, **kwargs):
                hidden_states = torch.nn.functional.pad(hidden_states, (0, 1, 0, 1), mode='circular')
                self.padding = 1  # Skip the original explicit zero-pad branch.
                try:
                    return _original(hidden_states, *args, **kwargs)
                finally:
                    self.padding = 0
            layer.forward = types.MethodType(forward, layer)
    return count


def gpu_state(root, stage, material, reserve_mib=0):
    result = subprocess.run(['nvidia-smi', '--query-gpu=memory.free,memory.used', '--format=csv,noheader,nounits'], check=True, capture_output=True, text=True)
    free, used = map(int, result.stdout.strip().split(','))
    sample = {'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'stage': stage, 'material': material, 'free_mib': free, 'used_mib': used}
    with (root/'gpu.jsonl').open('a') as output:
        output.write(json.dumps(sample) + '\n')
    if free < reserve_mib:
        raise RuntimeError(f'GPU free {free} MiB < required {reserve_mib} MiB')
    return sample


def save_maps(root, record, maps):
    import numpy as np
    from PIL import Image
    directory = root / 'out/bakeoff' / record['id']
    directory.mkdir(parents=True, exist_ok=True)
    for key in MAPS:
        value = np.asarray(maps[key])
        if not np.isfinite(value).all():
            raise ValueError(f'{key} contains nonfinite values')
        if value.ndim == 3 and value.shape[-1] == 1:
            value = value[..., 0]
        Image.fromarray(np.rint(np.clip(value, 0, 1) * 255).astype('uint8')).save(directory / (key + '.png'))
    record['files'] = {key: f'bakeoff/{record["id"]}/{key}.png' for key in MAPS}
    record['generation']['outcome'] = 'complete'


def filtered_maps(image, record):
    import numpy as np
    from scipy.ndimage import gaussian_filter
    rgb = np.asarray(image.convert('RGB'), dtype=np.float32) / 255
    luminance = rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
    size = rgb.shape[0]
    small = gaussian_filter(luminance, 2 * size / 1024, mode='wrap')
    large = gaussian_filter(luminance, 32 * size / 1024, mode='wrap')
    band = small - large
    scale = max(float(np.percentile(np.abs(band), 98)), .02)
    height = np.clip(.5 + band / (2 * scale), 0, 1)
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * size / 2
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * size / 2
    # Texture V points down; OpenGL-style tangent normal Y points up.
    normal = np.stack((-.025 * dx, .025 * dy, np.ones_like(dx)), axis=-1)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    ao = np.clip(1 - .8 * np.maximum(gaussian_filter(height, 8 * size / 1024, mode='wrap') - height, 0), .35, 1)
    roughness = np.clip(record['parameters']['roughness_base'] + .14 * (large - luminance), .35, 1)
    return dict(albedo=rgb, normal=normal * .5 + .5, height=height, roughness=roughness, metallic=np.zeros_like(height), ao=ao)


def run_stage(records, registry, root, stage):
    if __import__('platform').system() != 'Linux':
        raise RuntimeError('Model execution is allowed only on dev-ubuntu Linux')
    import numpy as np
    import torch
    from PIL import Image
    torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32 = True
    gpu_state(root, stage, 'load', 3000 if stage == 'stablematerials' else 8500)
    before_load = time.monotonic()
    if stage == 'sdxl':
        from diffusers import StableDiffusionXLPipeline
        pipe = StableDiffusionXLPipeline.from_pretrained(str(model_path(records, root, 'sdxl')), torch_dtype=torch.float16, variant='fp16', use_safetensors=True, local_files_only=True, add_watermarker=False)
        circular(pipe.unet); circular(pipe.vae)
        pipe.enable_attention_slicing()
        pipe.enable_model_cpu_offload()
    elif stage == 'stablematerials':
        from stable_materials_adapter import load_pipeline
        pipe = load_pipeline(str(model_path(records, root, stage)))
    else:
        from diffusers import MarigoldNormalsPipeline, MarigoldIntrinsicsPipeline
        cls = MarigoldNormalsPipeline if stage == 'normals' else MarigoldIntrinsicsPipeline
        pipe = cls.from_pretrained(str(model_path(records, root, stage)), torch_dtype=torch.float16, variant='fp16', use_safetensors=True, local_files_only=True)
        circular(pipe.unet); circular(pipe.vae)
        pipe.enable_attention_slicing()
        pipe.enable_model_cpu_offload()
    load_seconds = time.monotonic() - before_load
    for material, _, _ in MATERIALS:
        pipeline = 'sdxl-filter' if stage == 'sdxl' else ('stablematerials' if stage == 'stablematerials' else 'sdxl-marigold')
        record = next(r for r in records if r['pipeline'] == pipeline and r['parameters']['material'] == material)
        if record.get('skip_reason'):
            continue
        source = root/'out/bakeoff/_sources/sdxl'/f'{material}.png'
        if (stage in ('sdxl', 'stablematerials') and record['generation']['outcome'] == 'complete') or (stage in record['generation']):
            continue
        torch.cuda.empty_cache()
        sample = gpu_state(root, stage, material)
        torch.cuda.reset_peak_memory_stats()
        started = time.monotonic()
        if stage == 'sdxl':
            image = pipe(record['prompt'], negative_prompt=record['negative'], height=1024, width=1024,
                guidance_scale=record['parameters']['guidance_scale'], num_inference_steps=record['parameters']['steps'], generator=torch.Generator('cpu').manual_seed(record['seed'])).images[0]
            source.parent.mkdir(parents=True, exist_ok=True)
            image.save(source)
            save_maps(root, record, filtered_maps(image, record))
        elif stage == 'stablematerials':
            from stable_materials_adapter import generate
            maps = generate(pipe, record['prompt'], record['negative'], record['seed'], steps=record['parameters']['steps'])
            from scipy.ndimage import gaussian_filter
            h = maps['height']
            maps['ao'] = np.clip(1 - .8 * np.maximum(gaussian_filter(h, 4, mode='wrap') - h, 0), .35, 1)
            save_maps(root, record, maps)
        else:
            image = Image.open(source).convert('RGB')
            result = pipe(image, num_inference_steps=4, ensemble_size=1, processing_resolution=1024,
                match_input_resolution=True, generator=torch.Generator('cpu').manual_seed(record['parameters']['marigold']['seed']))
            prediction = result.prediction
            crop = (slice(None), slice(None))
            directory = root/'out/bakeoff'/record['id']
            directory.mkdir(parents=True, exist_ok=True)
            if stage == 'normals':
                n = prediction[0][crop]
                # Marigold +X right,+Y up,+Z toward viewer (OpenGL convention).
                maps = filtered_maps(image, record)
                maps['normal'] = n * .5 + .5
                save_maps(root, record, maps)
                record['generation']['outcome'] = 'pending'
                record['files'] = {}
            else:
                # prediction shape [N*target,H,W,C], target 0=albedo,
                # target 1 material channels roughness, metallicity, unused.
                maps = {k: np.asarray(Image.open(directory/(k+'.png')), dtype=np.float32)/255 for k in MAPS}
                maps['albedo'] = prediction[0][crop]
                maps['roughness'] = prediction[1][crop][...,0]
                maps['metallic'] = prediction[1][crop][...,1]
                save_maps(root, record, maps)
        record['generation'][stage] = {'seconds': round(time.monotonic()-started,3), 'load_seconds': round(load_seconds,3),
            'peak_allocated_mib': round(torch.cuda.max_memory_allocated()/1024**2,1), 'peak_reserved_mib': round(torch.cuda.max_memory_reserved()/1024**2,1), 'gpu_before': sample,
            'host': socket.gethostname(), 'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        print(material, stage, json.dumps(record['generation'][stage]), flush=True)
        write_registry(registry, records)
    del pipe
    gc.collect()
    torch.cuda.empty_cache()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare','download','sdxl','normals','appearance','stablematerials'])
    parser.add_argument('--registry', type=Path, default=Path('data/content/registry.jsonl'))
    parser.add_argument('--root', type=Path, default=Path.home()/'gothic-ai/materials')
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.registry)
        return
    args.root = args.root.resolve()
    args.root.mkdir(parents=True, exist_ok=True)
    os.environ['HF_HOME'] = str(args.root/'hf')
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
    records = read_registry(args.registry)
    if args.command == 'download':
        download(records, args.root)
    else:
        run_stage(records, args.registry, args.root, args.command)

if __name__ == '__main__':
    main()
