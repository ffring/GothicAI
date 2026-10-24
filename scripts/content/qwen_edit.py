#!/usr/bin/env python3
"""Edits of original textures with Qwen-Image-Edit-2511 on Ryzen: the second image editor of the remaster,
compared blind with ChatGPT gpt-image (the user's choice of 2026-10-24: the model under Apache 2.0, on
Ryzen through gq). Runs inside one gq job: starts ComfyUI on localhost, queues every edit, saves the
images and a JSON recipe beside each, stops the server.

    qwen_edit.py --comfy DIR --out DIR JOB.json

JOB.json: [{"name": "...", "image": "/abs/input.png", "prompt": "...", "seed": 1}, ...]
Each output is <name>.png and <name>.json (model files with SHA-256 prefixes, prompt, seed, steps, cfg).
Nothing of the original goes beyond the job's folders on Ryzen and the NAS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PORT = 48188
UNET = 'qwen-image-edit-2511-Q4_K_S.gguf'
CLIP = 'Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf'
VAE = 'qwen_image_vae.safetensors'
STEPS, CFG, SHIFT = 40, 4.0, 3.1   # the model card: 40 steps, true cfg 4


def graph(image: str, prompt: str, seed: int) -> dict:
    """ComfyUI API graph, as ComfyUI's own template image_qwen_image_edit_2511 without the Lightning LoRA:
    the edit encodes the input with Qwen2.5-VL and starts from its latent."""
    return {
        '1': {'class_type': 'UnetLoaderGGUF', 'inputs': {'unet_name': UNET}},
        '2': {'class_type': 'CLIPLoaderGGUF', 'inputs': {'clip_name': CLIP, 'type': 'qwen_image'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': VAE}},
        '4': {'class_type': 'LoadImage', 'inputs': {'image': image}},
        '5': {'class_type': 'FluxKontextImageScale', 'inputs': {'image': ['4', 0]}},
        '6': {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {'clip': ['2', 0], 'prompt': prompt,
                                                                    'vae': ['3', 0], 'image1': ['5', 0]}},
        '7': {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {'clip': ['2', 0], 'prompt': '',
                                                                    'vae': ['3', 0], 'image1': ['5', 0]}},
        '13': {'class_type': 'FluxKontextMultiReferenceLatentMethod',
               'inputs': {'conditioning': ['6', 0], 'reference_latents_method': 'index_timestep_zero'}},
        '14': {'class_type': 'FluxKontextMultiReferenceLatentMethod',
               'inputs': {'conditioning': ['7', 0], 'reference_latents_method': 'index_timestep_zero'}},
        '8': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['5', 0], 'vae': ['3', 0]}},
        '9': {'class_type': 'ModelSamplingAuraFlow', 'inputs': {'model': ['1', 0], 'shift': SHIFT}},
        '15': {'class_type': 'CFGNorm', 'inputs': {'model': ['9', 0], 'strength': 1.0}},
        '10': {'class_type': 'KSampler', 'inputs': {'model': ['15', 0], 'positive': ['13', 0], 'negative': ['14', 0],
                                                    'latent_image': ['8', 0], 'seed': seed, 'steps': STEPS,
                                                    'cfg': CFG, 'sampler_name': 'euler', 'scheduler': 'simple',
                                                    'denoise': 1.0}},
        '11': {'class_type': 'VAEDecode', 'inputs': {'samples': ['10', 0], 'vae': ['3', 0]}},
        '12': {'class_type': 'SaveImage', 'inputs': {'images': ['11', 0], 'filename_prefix': 'qwen-edit'}},
    }


def call(path: str, data: dict | None = None) -> dict:
    request = urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', method='POST' if data else 'GET',
                                     data=json.dumps(data).encode() if data else None,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b'{}')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 24), b''):
            h.update(block)
    return h.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('job', type=Path)
    parser.add_argument('--comfy', type=Path, required=True, help='ComfyUI checkout with ComfyUI-GGUF and its venv')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--models', type=Path, default=Path('/mnt/shared/gothic-ai/qwen-edit/models'))
    args = parser.parse_args(argv)
    edits = json.loads(args.job.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    inputs = args.comfy / 'input'
    inputs.mkdir(exist_ok=True)
    models = {'unet': (args.models / 'diffusion_models' / UNET), 'clip': (args.models / 'text_encoders' / CLIP),
              'vae': (args.models / 'vae' / VAE)}
    hashes = {k: {'file': p.name, 'sha256': digest(p)} for k, p in models.items()}
    python = args.comfy.parent / '.venv/bin/python'
    log = (args.out / 'comfy.log').open('w')
    server = subprocess.Popen([str(python), 'main.py', '--listen', '127.0.0.1', '--port', str(PORT),
                               '--output-directory', str(args.out / 'comfy-output'), '--disable-auto-launch'],
                              cwd=args.comfy, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(180):
            try:
                call('/system_stats')
                break
            except OSError:
                time.sleep(2)
        else:
            raise SystemExit('ComfyUI did not start, see comfy.log')
        for edit in edits:
            source = Path(edit['image'])
            shutil.copy(source, inputs / source.name)
            started = time.time()
            prompt_id = call('/prompt', {'prompt': graph(source.name, edit['prompt'], edit['seed'])})['prompt_id']
            while True:
                history = call(f'/history/{prompt_id}').get(prompt_id)
                if history and history.get('status', {}).get('completed'):
                    break
                if history and history.get('status', {}).get('status_str') == 'error':
                    raise SystemExit(f"{edit['name']}: {json.dumps(history['status'])[:2000]}")
                time.sleep(3)
            image = history['outputs']['12']['images'][0]
            produced = args.out / 'comfy-output' / image.get('subfolder', '') / image['filename']
            target = args.out / f"{edit['name']}.png"
            shutil.copy(produced, target)
            recipe = dict(name=edit['name'], model='Qwen-Image-Edit-2511 Q4_K_S (unsloth GGUF), Apache-2.0',
                          files=hashes, input=dict(file=source.name, sha256=digest(source)), prompt=edit['prompt'],
                          seed=edit['seed'], steps=STEPS, cfg=CFG, shift=SHIFT, sampler='euler/simple',
                          scale='FluxKontextImageScale', seconds=round(time.time() - started, 1))
            (args.out / f"{edit['name']}.json").write_text(json.dumps(recipe, indent=1))
            print(target, recipe['seconds'], flush=True)
    finally:
        server.terminate()
        try:
            server.wait(30)
        except subprocess.TimeoutExpired:
            server.kill()
    return 0


if __name__ == '__main__':
    sys.exit(main())
