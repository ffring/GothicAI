#!/usr/bin/env python3
"""Measure city-2 original brightness as scalars, optionally updating only that CSV column.

    python scripts/content/measure_luminance.py --extractor /path/to/texture-luminance \
        --game '/path/to/Gothic II' --write

The extractor decodes Gothic textures in memory, using texdump.cpp's ZenKit
path. Only alpha-weighted mean linear Rec.709 luminance reaches this script.
Original pixels are never exported or passed to material generation.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from map_textures import DEFAULT_MAPPING, FIELDS
from pilot import selected_rows


def read_mapping(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames not in (list(FIELDS), list(FIELDS[:-1])):
            raise ValueError("mapping must contain the current columns, optionally without target_luminance")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("mapping has a malformed CSV row")
    return rows


def parse_measurements(value, expected):
    reader = csv.DictReader(io.StringIO(value), strict=True)
    if reader.fieldnames != ["texture", "target_luminance"]:
        raise ValueError("extractor must return only texture,target_luminance")
    result = {}
    for row in reader:
        if None in row or any(item is None for item in row.values()):
            raise ValueError("extractor has a malformed CSV row")
        texture = row["texture"]
        if texture in result:
            raise ValueError(f"extractor repeats texture {texture}")
        luminance = float(row["target_luminance"])
        if not math.isfinite(luminance) or not 0 <= luminance <= 1:
            raise ValueError(f"{texture}: target_luminance must be finite and in [0, 1]")
        result[texture] = luminance
    if set(result) != set(expected):
        raise ValueError("extractor texture set differs from the city-2 pilot")
    return result


def measure(extractor, game, rows):
    names = [row["texture"] for row in selected_rows(rows, "city-2")]
    if not names:
        raise ValueError("city-2 pilot has no textures")
    result = subprocess.run([str(extractor), str(game), *names], check=True,
                            capture_output=True, text=True)
    return parse_measurements(result.stdout, names)


def update_mapping(path, rows, measurements):
    names = {row["texture"] for row in selected_rows(rows, "city-2")}
    if set(measurements) != names:
        raise ValueError("measurement texture set differs from the city-2 pilot")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or not 0 <= value <= 1 for value in measurements.values()):
        raise ValueError("target_luminance must be finite and in [0, 1]")
    # Preserve every pre-existing field, including any non-pilot scalar.
    updated = [dict(row, target_luminance=(format(measurements[row["texture"]], ".12g")
                    if row["texture"] in names else row.get("target_luminance", ""))) for row in rows]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(updated)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return updated


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extractor", type=Path, required=True)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--write", action="store_true", help="atomically add/update only target_luminance")
    args = parser.parse_args(argv)
    try:
        rows = read_mapping(args.mapping)
        values = measure(args.extractor, args.game, rows)
        if args.write:
            update_mapping(args.mapping, rows, values)
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(("texture", "target_luminance"))
        writer.writerows((texture, format(value, ".12g")) for texture, value in values.items())
    except (OSError, ValueError, csv.Error, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        print(f"measure_luminance: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
