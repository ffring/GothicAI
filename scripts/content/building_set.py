#!/usr/bin/env python3
"""Build a building's texture set from its remaster asset spec (stage 5 of docs/remaster-pipeline.md).

Input: `reports/content/remaster/<asset>/build.json` and the sources selected in the
asset's `asset.json`. Per texture: one Lanczos resize to 1024², narrow seam cuts on the
permitted axes, then colour by material:

- `target` — linear RGB gains so the texture's mean matches its own `target_oklab`;
- `group`  — one shared set of gains for several textures of one material, toward
             their area-weighted targets (the house-1 method);
- `match`  — gains so the texture's pixels outside `exclude_uv` match the corrected mean
             of another texture of the same material (boards around a window).

`relief` (0..1, default 1) scales the tilt of the filtered normal map: the CPU filter
normalises fine detail to full contrast, which turns wood grain into bark.

Numbers are guard rails: luminance outside the ratio range or clipped pixels are recorded,
never "fixed" here. No detail fit, Marigold or highlight shoulder. Maps come from the
existing CPU filter, the set is the base staging set with only these entries replaced.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image, ImageDraw

import bakeoff
import city_set
from market_house import apply_gains, correct_house_colour
from material_colour import _linear, oklab_to_linear
from material_lighting import measure_file as measure_light
import pack_materials
import quality_pilot as pilot
import registry as registry_module
import remaster
from seam_fix import seamless

LUMINANCE = np.array([.2126, .7152, .0722])
MODES = ('target', 'group', 'match')


def load_spec(asset: str) -> dict:
    spec = json.loads((remaster.asset_dir(asset) / 'build.json').read_text())
    for key in ('set', 'base', 'registry', 'textures'):
        if key not in spec:
            raise ValueError(f'build.json: missing {key}')
    for name, item in spec['textures'].items():
        if item.get('axes', 'uv') not in ('', 'u', 'v', 'uv'):
            raise ValueError(f'{name}: axes must be "", "u", "v" or "uv"')
        mode = item.get('colour', {}).get('mode')
        if mode not in MODES:
            raise ValueError(f'{name}: colour.mode must be one of {MODES}')
        if mode == 'match' and item['colour'].get('to') not in spec['textures']:
            raise ValueError(f'{name}: colour.to must name another texture of this set')
    return spec


def mask_outside(shape: tuple[int, int], exclude_uv) -> np.ndarray:
    h, w = shape
    mask = np.ones((h, w), dtype=bool)
    if exclude_uv:
        u0, v0, u1, v1 = exclude_uv
        mask[round(v0 * h):round(v1 * h), round(u0 * w):round(u1 * w)] = False
    return mask


def gains_for_mean(pixels: np.ndarray, mask: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Per-channel gain g so that mean(min(linear*g, 1)) over the mask equals target."""
    linear = _linear(pixels.astype(float) / 255)[mask]
    gains = np.ones(3)
    for c in range(3):
        values = linear[:, c]
        lo, hi = 0., 1.
        while np.minimum(values * hi, 1).mean() < target[c] and hi < 65536:
            hi *= 2
        for _ in range(60):
            mid = (lo + hi) / 2
            if np.minimum(values * mid, 1).mean() < target[c]:
                lo = mid
            else:
                hi = mid
        gains[c] = (lo + hi) / 2
    return gains


def clipped_fraction(pixels: np.ndarray, gains) -> float:
    linear = _linear(pixels.astype(float) / 255) * np.asarray(gains)
    return float(np.any(linear > 1, axis=2).mean())


def correct_colours(images: dict, spec: dict, targets: dict) -> tuple[dict, dict]:
    corrected, info = {}, {}
    textures = spec['textures']
    groups = {}
    for name, item in textures.items():
        colour = item['colour']
        if colour['mode'] == 'target':
            out, meta = correct_house_colour({name: images[name]}, {name: 1.}, {name: targets[name]})
            corrected[name] = out[name]
            info[name] = dict(mode='target', gains=meta['channel_gains'],
                              clipped_fraction=clipped_fraction(images[name], meta['channel_gains']))
        elif colour['mode'] == 'group':
            groups.setdefault(colour['group'], []).append(name)
    for group, names in groups.items():
        areas = {n: float(textures[n]['colour']['area_m2']) for n in names}
        out, meta = correct_house_colour({n: images[n] for n in names}, areas, {n: targets[n] for n in names})
        corrected.update(out)
        for n in names:
            info[n] = dict(mode='group', group=group, gains=meta['channel_gains'], area_m2=areas[n],
                           clipped_fraction=clipped_fraction(images[n], meta['channel_gains']))
    pending = [n for n, item in textures.items() if item['colour']['mode'] == 'match']
    while pending:
        progressed = False
        for name in list(pending):
            colour = textures[name]['colour']
            if colour['to'] not in corrected:
                continue
            reference = _linear(corrected[colour['to']].astype(float) / 255).mean(axis=(0, 1))
            mask = mask_outside(images[name].shape[:2], colour.get('exclude_uv'))
            gains = gains_for_mean(images[name], mask, reference)
            corrected[name] = apply_gains(images[name], gains)
            after = _linear(corrected[name].astype(float) / 255)[mask].mean(axis=0)
            error = float(np.abs(after - reference).max())
            if error > .005:
                raise ValueError(f'{name}: matched colour error {error:.6f} exceeds .005')
            info[name] = dict(mode='match', to=colour['to'], exclude_uv=colour.get('exclude_uv'),
                              gains=gains.tolist(), reference_linear_rgb=reference.tolist(),
                              matched_linear_rgb=after.tolist(), max_error=error,
                              masked_fraction=float(mask.mean()),
                              clipped_fraction=clipped_fraction(images[name], gains))
            pending.remove(name)
            progressed = True
        if not progressed:
            raise ValueError(f'match cycle or missing reference: {pending}')
    return corrected, info


def source_attempts(manifest: dict, texture: str) -> list[dict]:
    attempts = []
    for source in manifest['sources']:
        if source.get('texture') != texture:
            continue
        meta_path = (remaster.sources_root() / source['file']).with_suffix('.json')
        meta = json.loads(meta_path.read_text())
        attempts.append(dict(attempt=source['attempt'], prompt=meta['prompt'], utc=meta.get('utc'),
            seconds=meta.get('seconds'), source_png=source['file'], source_sha256=source['sha256'],
            model=source.get('model'), parent=source.get('parent')))
    return sorted(attempts, key=lambda a: a['attempt'])


def prepare(asset: str, spec: dict) -> tuple[list[dict], dict]:
    manifest = remaster.load_manifest(asset)
    names = list(spec['textures'])
    recipes = {r['replaces'][0]: r for r in city_set.recipes(textures=names)}
    prepared_dir = remaster.work_root(asset) / 'prepared' / spec['set']
    prepared_dir.mkdir(parents=True, exist_ok=True)
    images, records = {}, []
    for name in names:
        chosen = manifest['selected'].get(name)
        if chosen is None:
            raise ValueError(f'{name}: no selected source in asset.json')
        source = next(s for s in manifest['sources'] if s['file'] == chosen['file'])
        path = remaster.sources_root() / source['file']
        if remaster.sha256(path) != source['sha256']:
            raise ValueError(f'{path}: SHA-256 differs from asset.json')
        axes = spec['textures'][name].get('axes', 'uv')
        with Image.open(path) as image:
            resized = image.convert('RGB').resize((1024, 1024), Image.Resampling.LANCZOS)
            joined, seam = seamless(resized, axes=axes, overlap_fraction=1/64)
        images[name] = np.asarray(joined)
        record = recipes[name]
        record['id'] = spec['set'] + '-' + Path(name).stem.lower().replace('_', '-')
        record['prompt'] = json.loads(path.with_suffix('.json').read_text())['prompt']
        record['parameters'].update(set=spec['set'], material=record['id'], scale=1., tiling_axes=axes,
            tileable=bool(axes), antitile='off', source_attempt=source['attempt'],
            input='image_gen edit of the accepted remaster concept; no original texture pixels',
            colour_policy=f"remaster building_set: {spec['textures'][name]['colour']['mode']}",
            luminance_policy='guard rail only: ratio to the original target is recorded, not enforced',
            source_size_policy='one Lanczos resize to 1024 square, then narrow permitted-axis seam cuts')
        record['generation'] = dict(outcome='complete', attempts=source_attempts(manifest, name),
            selected_attempt=source['attempt'], selection_reason=chosen.get('reason'),
            source_policy='image_gen edits of the accepted concept; immutable PNGs with C2PA',
            model_identity=source.get('model'), seam=seam, remaster_asset=asset,
            detail_fit_applied=False, marigold_applied=False, shoulder_applied=False)
        records.append(record)
    targets = {r['replaces'][0]: oklab_to_linear(r['parameters']['target_oklab']) for r in records}
    corrected, colour = correct_colours(images, spec, targets)
    low, high = spec.get('guard', {}).get('luminance_ratio', [.75, 1.25])
    clip_max = spec.get('guard', {}).get('clipped_max', .01)
    report = {}
    for record in records:
        name = record['replaces'][0]
        out = prepared_dir / (Path(name).stem + '.png')
        Image.fromarray(corrected[name]).save(out)
        mean = _linear(corrected[name].astype(float) / 255).mean(axis=(0, 1))
        ratio = float(mean @ LUMINANCE / (targets[name] @ LUMINANCE))
        measurement = dict(target_linear_rgb=targets[name].tolist(), mean_linear_rgb=mean.tolist(),
            luminance_ratio=ratio, luminance_within_guard=low <= ratio <= high,
            clipped_within_guard=colour[name]['clipped_fraction'] <= clip_max)
        record['generation'].update(colour=colour[name], colour_measurement=measurement,
            prepared_sha256=pilot.file_digest(out), detector=measure_light(out))
        report[name] = dict(colour[name], **measurement)
    return records, report


def scale_relief(normal_map: np.ndarray, relief: float) -> np.ndarray:
    """Scale the tangent-space tilt of a 0..1 encoded normal map; 0 is flat, 1 unchanged."""
    if not 0 <= relief <= 1:
        raise ValueError(f'relief must be within 0..1, got {relief}')
    vector = normal_map.astype(np.float64) * 2 - 1
    vector[..., :2] *= relief
    vector /= np.linalg.norm(vector, axis=-1, keepdims=True)
    return (vector * .5 + .5).astype(normal_map.dtype)


def pack(records: list[dict], spec: dict, derived_root: Path) -> dict:
    base = remaster.data_root() / 'content-staging' / spec['base']
    output = pack_materials.staging_output(base.parent / spec['set'])
    if output.exists():
        raise ValueError(f'{output}: staging set exists; choose a new set name')
    shutil.copytree(base, output, symlinks=False)
    index_path = output / 'materials/materials.json'
    before = json.loads(index_path.read_text())
    index = deepcopy(before)
    write_dds = pack_materials.load_compressor()
    names = {r['replaces'][0] for r in records}
    missing = names - before.keys()
    if missing:
        raise ValueError(f'{spec["base"]} has no entries for {sorted(missing)}')
    for record in records:
        maps = pack_materials.read_maps(record, derived_root)
        directory = output / 'materials' / record['id']
        directory.mkdir()
        write_dds(directory / 'albedo.dds', maps['albedo'])
        write_dds(directory / 'normal.dds', maps['normal'], normal=True)
        write_dds(directory / 'orm.dds', np.stack([maps[k] for k in ('ao', 'roughness', 'metallic')], axis=2))
        name = record['replaces'][0]
        index[name] = dict(before[name], **{k: f'{record["id"]}/{k}.dds' for k in ('albedo', 'normal', 'orm')},
                           scale=1., antitile='off')
    for name in before.keys() - names:
        for channel in ('albedo', 'normal', 'orm'):
            relative = Path('materials') / before[name][channel]
            if pilot.file_digest(base / relative) != pilot.file_digest(output / relative):
                raise ValueError(f'{name}: unrelated DDS changed')
    index_path.write_text(json.dumps(index, indent=2) + '\n')
    return dict(entries=len(index), replaced=sorted(names), unchanged_entries=len(index) - len(names))


def contact_sheet(records: list[dict], derived_root: Path, target: Path) -> None:
    side, header = 400, 32
    cols = min(3, len(records))
    rows = -(-len(records) // cols)
    sheet = Image.new('RGB', (side * cols, (side + header) * rows), '#252626')
    draw = ImageDraw.Draw(sheet)
    for i, record in enumerate(records):
        x, y = i % cols * side, i // cols * (side + header)
        with Image.open(derived_root / record['files']['albedo']) as image:
            sheet.paste(image.resize((side, side), Image.Resampling.LANCZOS), (x, y + header))
        draw.text((x + 4, y + 8), Path(record['replaces'][0]).stem.removeprefix('NW_CITY_'), fill='white')
    for quality in range(90, 39, -5):
        data = BytesIO()
        sheet.save(data, 'JPEG', quality=quality, optimize=True)
        if data.tell() <= 1_000_000:
            target.write_bytes(data.getvalue())
            return
    raise ValueError('albedo sheet exceeds 1 MB')


def build(asset: str, spec: dict, records: list[dict]) -> dict:
    derived_root = remaster.data_root() / 'content-staging' / f'{asset}-sources'
    for record in records:
        path = remaster.work_root(asset) / 'prepared' / spec['set'] / (Path(record['replaces'][0]).stem + '.png')
        with Image.open(path) as image:
            record['_maps'] = bakeoff.filtered_maps(image, record)
        relief = float(spec['textures'][record['replaces'][0]].get('relief', 1.))
        if relief != 1.:
            record['_maps']['normal'] = scale_relief(record['_maps']['normal'], relief)
        record['parameters']['relief'] = relief
        lo, hi = record['parameters']['roughness_range']
        record['_maps']['roughness'] = np.clip(record['_maps']['roughness'], np.ceil(lo * 255) / 255,
                                               np.floor(hi * 255) / 255)
        pilot.save_maps(record, derived_root)
        record['generation']['albedo_sha256'] = pilot.file_digest(derived_root / record['files']['albedo'])
        registry_module.validate_entry(record, content_root=derived_root, source_root=remaster.sources_root())
    registry_path = remaster.ROOT / spec['registry']
    previous = registry_path.read_text() if registry_path.exists() else ''
    ids = {json.loads(line)['id'] for line in previous.splitlines() if line.strip()}
    if any(r['id'] in ids for r in records):
        raise ValueError(f'{registry_path}: already contains set {spec["set"]}')
    invariants = pack(records, spec, derived_root)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(previous + ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n'
                                                for r in records))
    contact_sheet(records, derived_root, remaster.asset_dir(asset) / f'{spec["set"]}-albedos.jpg')
    manifest = remaster.load_manifest(asset)
    manifest['build'] = dict(set=spec['set'], base=spec['base'],
        date=datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        albedo_sha256={r['replaces'][0]: r['generation']['albedo_sha256'] for r in records}, **invariants)
    remaster.save_manifest(asset, manifest)
    return invariants


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'build'))
    parser.add_argument('asset')
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.asset)
        records, report = prepare(args.asset, spec)
        for name, row in report.items():
            print(json.dumps(dict(texture=name, **row), sort_keys=True), flush=True)
        if args.command == 'build':
            print(json.dumps(build(args.asset, spec, records), sort_keys=True), flush=True)
    except (OSError, ValueError, KeyError, StopIteration) as error:
        print(f'building_set: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
