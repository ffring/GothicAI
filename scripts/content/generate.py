#!/usr/bin/env python3
"""Generate text-only material sets from the mapping CSV and the style bible.

prepare is CPU-only; run uses the pinned bakeoff FLUX adapter on dev-ubuntu.
All attempts, including rejected tiles, remain in each material's registry row.
"""
from __future__ import annotations

import argparse
import csv
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import re
import time

import bakeoff

ROOT = Path(__file__).resolve().parents[2]


def read_style(path):
    text = path.read_text()
    classes = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) != 6 or not re.fullmatch(r'`[a-z_]+`', cells[0]):
            continue
        def pair(value):
            values = [float(v.replace(',', '.')) for v in re.split('[–—]', value)]
            if len(values) != 2 or not 0 <= values[0] < values[1] <= 1:
                raise ValueError(f'invalid style range: {value}')
            return values
        classes[cells[0].strip('`')] = dict(tile_m=float(cells[2].replace(',', '.')),
            luminance=pair(cells[3]), roughness=pair(cells[4]), prompt=cells[5])
    template = re.search(r'^`(<ядро класса>, <состояние из записи>, .+)`$', text, re.M)
    if not classes or template is None:
        raise ValueError('style bible has no class table or prompt template')
    limits = re.search(r'Средняя насыщенность HSV S.*?не больше ([0-9]+(?:[,\.][0-9]+)?).*?`roof_tiles`.*?не больше ([0-9]+(?:[,\.][0-9]+)?)', text)
    if limits is None:
        raise ValueError('style bible has no HSV saturation limits')
    default_limit, roof_limit = (float(v.replace(',', '.')) for v in limits.groups())
    for name, style in classes.items():
        style['saturation_limit'] = roof_limit if name == 'roof_tiles' else default_limit
    return classes, template[1]


def seed_for(texture, attempt=1):
    # Explicit byte order and encoding make seeds stable across Python/hosts.
    return int.from_bytes(hashlib.sha256(f'{texture.upper()}:{attempt}'.encode('utf-8')).digest()[:4], 'big')


def recipes(mapping, bible, set_name):
    classes, template = read_style(bible)
    with mapping.open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    result = []
    seen = set()
    for row in rows:
        if row['decision'] != 'include':
            continue
        texture = row['texture']
        if texture in seen:
            raise ValueError(f'duplicate included texture: {texture}')
        seen.add(texture)
        style = classes[row['class']]
        resource_id = set_name + '-' + Path(texture).stem.lower().replace('_', '-')
        prompt = template.replace('<ядро класса>', style['prompt']).replace('<состояние из записи>', row['state'])
        if row['class'] in ('dirt_path', 'mud'):
            prompt = prompt.replace(', grime in the cracks', '')
        result.append(dict(id=resource_id, kind='material', replaces=[texture], pipeline='flux-filter',
            models=[dict(id=bakeoff.MODELS['flux'][0], revision=bakeoff.FLUX_REVISION, license='Apache-2.0')],
            prompt=prompt, negative='', seed=seed_for(texture), status='draft', files={},
            parameters=dict(set=set_name, material=resource_id, material_class=row['class'], state=row['state'],
                scale=float(row['scale']), tile_m=style['tile_m'], luminance_range=style['luminance'],
                saturation_limit=style['saturation_limit'],
                roughness_range=style['roughness'], roughness_base=sum(style['roughness'])/2,
                resolution=1024, steps=4, guidance_scale=0.0, max_sequence_length=256,
                quantization=bakeoff.FLUX_QUANTIZATION, tiling=bakeoff.FLUX_TILING, torch_dtype='bfloat16',
                tileable=True, negative_prompt_used=False, metallic=0,
                height_filter=dict(sigma_small_px_at_1024=2, sigma_large_px_at_1024=32, normal_strength=.025),
                normal_convention='OpenGL: X right, Y up, Z toward viewer; DDS pack flips Y for engine texture-V basis',
                input='text only; no original pixels', seed_algorithm='first 32 bits SHA256(uppercase texture + colon + 1-based attempt), big endian',
                mapping_sha256=hashlib.sha256(mapping.read_bytes()).hexdigest(), bible_sha256=hashlib.sha256(bible.read_bytes()).hexdigest()),
            generation=dict(outcome='pending', attempts=[])))
    if not result:
        raise ValueError('mapping has no included materials')
    return result


def prepare(registry, mapping, bible, set_name):
    records = bakeoff.read_registry(registry) if registry.exists() else []
    existing = {r['id']: r for r in records}
    for recipe in recipes(mapping, bible, set_name):
        if recipe['id'] in existing:
            old = existing[recipe['id']]
            acknowledged = old['generation'].get('prepared_inputs') == input_signature(recipe)
            if not acknowledged and any(old[key] != recipe[key] for key in ('parameters', 'prompt', 'models', 'replaces')):
                raise ValueError(f'{recipe["id"]}: inputs changed; use a new set name')
        else:
            records.append(recipe)
    bakeoff.write_registry(registry, records)
    return records


def input_signature(recipe):
    value = {k: recipe[k] for k in ('parameters', 'prompt', 'models', 'replaces')}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def archive_recipe(record):
    generation = record['generation']
    if 'recipes' not in generation:
        generation['recipes'] = [dict(revision=1, **deepcopy({k: record[k] for k in ('prompt', 'negative', 'models', 'parameters')}))]
        for attempt in generation['attempts']:
            attempt['recipe_revision'] = 1
    return generation['recipes']


def refresh(records, mapping, bible, set_name, textures, reason):
    """Explicitly authorize three new attempts only for the selected textures.

    Other rows retain their actual generation prompt, source maps and seed;
    only their colour postprocess policy changes. Prior recipes remain pinned.
    """
    fresh = {r['id']: r for r in recipes(mapping, bible, set_name)}
    selected = set(textures)
    available = {r['replaces'][0] for r in fresh.values()}
    if not selected or selected - available:
        raise ValueError('refresh requires known include texture names')
    for record in records:
        if record['id'] not in fresh:
            continue
        recipe = fresh[record['id']]
        history = archive_recipe(record)
        generation = record['generation']
        signature = input_signature(recipe)
        if generation.get('prepared_inputs') == signature:
            continue  # Repeating refresh cannot grant another attempt budget.
        generation['prepared_inputs'] = signature
        if record['replaces'][0] in selected:
            generation.setdefault('previous_selections', []).append(deepcopy({
                'seed': record['seed'], 'files': record['files'],
                'brightness_correction': generation.get('brightness_correction'),
                'visual_review': generation.get('visual_review'), 'reason': reason}))
            for key in ('prompt', 'negative', 'models', 'parameters'):
                record[key] = deepcopy(recipe[key])
            revision = len(history)+1
            history.append(dict(revision=revision, **deepcopy({k: record[k] for k in ('prompt', 'negative', 'models', 'parameters')})))
            generation.update(outcome='pending', recipe_revision=revision,
                series_start=len(generation['attempts']), attempt_limit=len(generation['attempts'])+3,
                refresh_reason=reason)
            generation.pop('visual_review', None)
            record['files'] = {}
        else:
            record['parameters'].update(saturation_limit=recipe['parameters']['saturation_limit'],
                postprocess_bible_sha256=recipe['parameters']['bible_sha256'])


def postprocess(records, root, set_name):
    """Apply only saturation to completed, unregenerated selected albedos."""
    import numpy as np
    from PIL import Image
    from material_colour import limit_saturation
    for record in records:
        if record['parameters'].get('set') != set_name or record['generation']['outcome'] != 'complete':
            continue
        generation = record['generation']
        limit = record['parameters']['saturation_limit']
        signature = generation.get('prepared_inputs', '')
        if generation.get('saturation_correction', {}).get('prepared_inputs') == signature:
            continue
        source = root/'out'/record['files']['albedo']
        with Image.open(source) as image:
            pixels, correction = limit_saturation(np.asarray(image.convert('RGB'))/255, limit)
        relative = Path(set_name)/record['id']/('colour-'+signature[:12])/'albedo.png'
        target = root/'out'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(pixels).save(target)
        correction.update(input=record['files']['albedo'], output=relative.as_posix(), prepared_inputs=signature)
        generation.setdefault('postprocess', []).append(deepcopy(correction))
        generation['saturation_correction'] = correction
        record['files']['albedo'] = relative.as_posix()


def corrected_maps(image, record):
    import numpy as np
    from PIL import Image
    from material_colour import limit_saturation
    rgb = np.asarray(image.convert('RGB'), dtype=np.float32) / 255
    linear = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
    weights = np.array([.2126, .7152, .0722])
    before = float((linear @ weights).mean())
    lo, hi = record['parameters']['luminance_range']
    # Aim at the class midpoint when correcting, leaving room for 8-bit rounding.
    target = (lo+hi)/2 if not lo <= before <= hi else before
    gain = 1.0
    if target != before:
        left, right = 0., 1.
        while float((np.clip(linear*right, 0, 1) @ weights).mean()) < target and right < 65536:
            right *= 2
        for _ in range(40):
            gain = (left+right)/2
            if float((np.clip(linear*gain, 0, 1) @ weights).mean()) < target:
                left = gain
            else:
                right = gain
    adjusted = np.clip(linear*gain, 0, 1)
    encoded = np.where(adjusted <= .0031308, adjusted*12.92, 1.055*adjusted**(1/2.4)-.055)
    pixels = np.rint(encoded*255).astype('uint8')
    normalized = pixels.astype(np.float32)/255
    quantized_linear = np.where(normalized <= .04045, normalized/12.92, ((normalized+.055)/1.055)**2.4)
    after = float((quantized_linear @ weights).mean())
    correction = dict(method='uniform linear RGB gain with clipping; no spatial edits',
        linear_mean_before=before, linear_mean_after=after, target=target, gain=gain,
        clipped_channel_fraction=float((linear*gain > 1).mean()), range=[lo, hi])
    maps = bakeoff.filtered_maps(Image.fromarray(pixels), record)
    pixels, saturation = limit_saturation(pixels.astype(np.float64)/255, record['parameters']['saturation_limit'])
    maps['albedo'] = pixels.astype(np.float32)/255
    correction['saturation'] = saturation
    rlo, rhi = record['parameters']['roughness_range']
    # Quantized endpoints must also lie inside the class range.
    maps['roughness'] = np.clip(maps['roughness'], np.ceil(rlo*255)/255, np.floor(rhi*255)/255)
    rejection = None
    if not lo <= after <= hi:
        rejection = 'brightness correction could not reach class range'
    elif correction['clipped_channel_fraction'] > .4:
        rejection = 'more than 40% of colour channels clipped after level correction'
    return maps, correction, rejection


def save_attempt(root, record, maps, number):
    import numpy as np
    from PIL import Image
    relative = Path(record['parameters']['set']) / record['id'] / f'attempt-{number}'
    directory = root/'out'/relative
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in maps.items():
        Image.fromarray(np.rint(np.clip(value, 0, 1)*255).astype('uint8')).save(directory/f'{name}.png')
    return {name: (relative/f'{name}.png').as_posix() for name in bakeoff.MAPS}


def reject(records, resource_id, reason):
    record = next(r for r in records if r['id'] == resource_id)
    attempts = record['generation']['attempts']
    if record['generation']['outcome'] != 'complete' or not attempts:
        raise ValueError('only a completed tile can be rejected')
    if len(attempts) >= record['generation'].get('attempt_limit', 3):
        raise ValueError('three attempts exhausted; a decision is required')
    attempts[-1].update(outcome='rejected', reason=reason)
    record['generation']['outcome'] = 'pending'
    record['files'] = {}


def select_attempt(record, attempt, note=''):
    record.update(seed=attempt['seed'], files=deepcopy(attempt['files']))
    generation = record['generation']
    generation.update(outcome='complete', selected_attempt=attempt['number'],
        brightness_correction=deepcopy(attempt['brightness_correction']))
    if 'saturation' in attempt['brightness_correction']:
        generation['saturation_correction'] = deepcopy(attempt['brightness_correction']['saturation'])
        generation['saturation_correction']['prepared_inputs'] = generation.get('prepared_inputs', '')
    if note:
        generation['selection_note'] = note


def run(records, registry, root, set_name):
    if platform.system() != 'Linux' or root.resolve() != (Path.home()/'gothic-ai/materials').resolve():
        raise RuntimeError('models run only in ~/gothic-ai/materials on dev-ubuntu')
    import torch
    pending = [r for r in records if r['parameters'].get('set') == set_name and r['generation']['outcome'] == 'pending']
    if not pending:
        print('No pending materials', flush=True)
        return
    torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32 = True
    bakeoff.gpu_state(root, set_name, 'load', 10000)
    started = time.monotonic()
    pipe, embeddings, encoding = bakeoff.load_flux(records, root, pending)
    print(f'FLUX ready in {time.monotonic()-started:.1f}s', flush=True)
    for record in pending:
        attempts = record['generation']['attempts']
        while len(attempts) < record['generation'].get('attempt_limit', 3):
            number = len(attempts)+1
            seed = seed_for(record['replaces'][0], number)
            attempt = dict(number=number, seed=seed, outcome='started',
                recipe_revision=record['generation'].get('recipe_revision', 1),
                utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            attempts.append(attempt)
            bakeoff.write_registry(registry, records)
            prompt, pooled = embeddings[record['parameters']['material']]
            pipe.transformer._tile_step = 0
            torch.cuda.reset_peak_memory_stats()
            start = time.monotonic()
            image = pipe(height=1024, width=1024, guidance_scale=0., num_inference_steps=4,
                generator=torch.Generator('cpu').manual_seed(seed), prompt_embeds=prompt.to('cuda:0', dtype=pipe.transformer.dtype),
                pooled_prompt_embeds=pooled.to('cuda:0', dtype=pipe.transformer.dtype), max_sequence_length=256).images[0]
            maps, correction, reason = corrected_maps(image, record)
            files = save_attempt(root, record, maps, number)
            image.save(root/'out'/Path(files['albedo']).parent/'source.png')
            attempt.update(seconds=round(time.monotonic()-start, 3), brightness_correction=correction, files=files,
                outcome='rejected' if reason else 'complete', peak_allocated_mib=round(torch.cuda.max_memory_allocated()/1024**2,1))
            if reason:
                attempt['reason'] = reason
            else:
                select_attempt(record, attempt)
                record['generation'].update(encoding=encoding[record['parameters']['material']], host='dev-ubuntu')
            bakeoff.write_registry(registry, records)
            print(record['id'], json.dumps({k: v for k, v in attempt.items() if k != 'files'}), flush=True)
            if not reason:
                break
        if record['generation']['outcome'] != 'complete':
            candidates = [a for a in attempts[record['generation'].get('series_start', 0):] if a.get('files')]
            if not candidates:
                raise RuntimeError(f'{record["id"]}: no generated candidate')
            best = min(candidates, key=lambda a: abs(a['brightness_correction']['linear_mean_after']-a['brightness_correction']['target']))
            select_attempt(record, best, 'Attempt limit reached; best available candidate retained')
            bakeoff.write_registry(registry, records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'run', 'reject', 'refresh', 'postprocess'])
    parser.add_argument('--registry', type=Path, default=ROOT/'data/content/registry.jsonl')
    parser.add_argument('--mapping', type=Path, default=ROOT/'data/content/city-materials.csv')
    parser.add_argument('--bible', type=Path, default=ROOT/'docs/content-style.md')
    parser.add_argument('--set', default='city-1', dest='set_name')
    parser.add_argument('--root', type=Path, default=Path.home()/'gothic-ai/materials')
    parser.add_argument('--id')
    parser.add_argument('--reason')
    parser.add_argument('--texture', action='append', default=[])
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', args.set_name):
        parser.error('unsafe set name')
    if args.command == 'prepare':
        records = prepare(args.registry, args.mapping, args.bible, args.set_name)
        print(f'{sum(r["parameters"].get("set") == args.set_name for r in records)} set materials prepared')
    else:
        records = bakeoff.read_registry(args.registry)
        if args.command == 'refresh':
            if not args.reason:
                parser.error('refresh requires --reason and --texture')
            refresh(records, args.mapping, args.bible, args.set_name, args.texture, args.reason)
            bakeoff.write_registry(args.registry, records)
        elif args.command == 'postprocess':
            postprocess(records, args.root, args.set_name)
            bakeoff.write_registry(args.registry, records)
        elif args.command == 'reject':
            if not args.id or not args.reason:
                parser.error('reject requires --id and --reason')
            reject(records, args.id, args.reason)
            bakeoff.write_registry(args.registry, records)
        else:
            run(records, args.registry, args.root, args.set_name)


if __name__ == '__main__':
    main()
