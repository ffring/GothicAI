#!/usr/bin/env python3
"""Check city material decisions against the world texture census.

    python3 scripts/content/map_textures.py check
    python3 scripts/content/map_textures.py update

``update`` refreshes area, UV density and material scale, preserving the manual
decisions. It writes only after validating all 71 city and harbour textures.
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING = ROOT / "data/content/city-materials.csv"
DEFAULT_CENSUS = ROOT / "reports/content/2026-10-world-textures/newworld.csv"
FIELDS = (
    "texture", "decision", "class", "state", "scale", "area_m2",
    "uv_m_per_unit_median", "reason",
)
CENSUS_FIELDS = frozenset({
    "world", "texture", "area_m2", "uv_m_per_unit_median", "tiled_area_fraction",
})
TILE_METERS = {name: Decimal(meters) for name, meters in {
    "cobble": "2", "flagstone": "3", "masonry_dressed": "3",
    "masonry_rubble": "2", "plaster": "2", "planks_floor": "2",
    "planks_wall": "2", "timber_beam": "1.5", "roof_shingle": "2",
    "roof_tiles": "2", "thatch": "2", "dirt_path": "3", "mud": "3",
}.items()}
SCOPE_SIZE = 71
MIN_TILED_FRACTION = Decimal("0.6")
STATE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*(?: [A-Za-z]+(?:[-'][A-Za-z]+)*){1,4}\Z")


class MappingError(ValueError):
    """An invalid mapping or census, with a location suitable for the CLI."""


def _read_csv(path: Path, *, mapping: bool) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, None)
            if header is None:
                raise MappingError(f"{path}: empty CSV")
            if len(header) != len(set(header)):
                raise MappingError(f"{path}: duplicate CSV column")
            if mapping and header != list(FIELDS):
                raise MappingError(f"{path}: expected CSV columns in order: {','.join(FIELDS)}")
            if not mapping and not CENSUS_FIELDS <= set(header):
                missing = ", ".join(sorted(CENSUS_FIELDS - set(header)))
                raise MappingError(f"{path}: missing census columns: {missing}")
            rows = []
            for values in reader:
                if len(values) != len(header):
                    raise MappingError(
                        f"{path}:{reader.line_num}: expected {len(header)} CSV fields, got {len(values)}"
                    )
                rows.append(dict(zip(header, values)))
            return rows
    except (OSError, UnicodeError, csv.Error) as error:
        raise MappingError(f"{path}: {error}") from error


def _number(value: str, field: str, *, positive: bool = False) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise MappingError(f"{field}: expected a finite number, got {value!r}") from error
    if not number.is_finite() or (number <= 0 if positive else number < 0):
        sign = "positive" if positive else "nonnegative"
        raise MappingError(f"{field}: expected a finite {sign} number, got {value!r}")
    return number


def _scope(texture: str) -> bool:
    return texture.startswith(("NW_CITY_", "NW_HARBOUR_"))


def read_census(path: Path) -> dict[str, dict[str, str]]:
    """Read the fixed NEWWORLD scope, rejecting duplicate or invalid sources."""
    census = {}
    for line, row in enumerate(_read_csv(path, mapping=False), 2):
        texture = row["texture"]
        if row["world"] != "NEWWORLD.ZEN" or not _scope(texture):
            continue
        where = f"{path}:{line}: {texture}"
        if texture in census:
            raise MappingError(f"{where}: duplicate census texture")
        _number(row["area_m2"], f"{where}: area_m2")
        _number(row["uv_m_per_unit_median"], f"{where}: uv_m_per_unit_median", positive=True)
        fraction = _number(row["tiled_area_fraction"], f"{where}: tiled_area_fraction")
        if fraction > 1:
            raise MappingError(f"{where}: tiled_area_fraction must be at most 1")
        census[texture] = row
    if len(census) != SCOPE_SIZE:
        raise MappingError(f"{path}: expected {SCOPE_SIZE} city/harbour textures, got {len(census)}")
    return census


def material_scale(uv: Decimal, material: str) -> Decimal:
    """UV metres per unit / physical tile metres, rounded to hundredths."""
    with localcontext() as context:
        context.prec = max(28, len(uv.as_tuple().digits) + abs(uv.adjusted()) + 10)
        return (uv / TILE_METERS[material]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _check_coverage(rows: list[dict[str, str]], census: dict[str, dict[str, str]], path: Path) -> None:
    names = set()
    for line, row in enumerate(rows, 2):
        texture = row["texture"]
        if texture in names:
            raise MappingError(f"{path}:{line}: duplicate mapping texture {texture!r}")
        names.add(texture)
    missing = sorted(census.keys() - names)
    extra = sorted(names - census.keys())
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing textures: {', '.join(missing)}")
        if extra:
            details.append(f"extra textures: {', '.join(extra)}")
        raise MappingError(f"{path}: " + "; ".join(details))


def validate_mapping(
    rows: list[dict[str, str]], census: dict[str, dict[str, str]], *, path: Path
) -> None:
    """Validate manual decisions, source measurements and derived scales."""
    _check_coverage(rows, census, path)
    for line, row in enumerate(rows, 2):
        texture = row["texture"]
        where = f"{path}:{line}: {texture}"
        if row["decision"] not in ("include", "exclude"):
            raise MappingError(f"{where}: decision must be include or exclude")
        if not row["reason"].strip():
            raise MappingError(f"{where}: reason must be nonempty")
        source = census[texture]
        for field in ("area_m2", "uv_m_per_unit_median"):
            actual = _number(row[field], f"{where}: {field}", positive=field != "area_m2")
            if actual != Decimal(source[field]):
                raise MappingError(f"{where}: stale {field}: {row[field]!r}; census has {source[field]!r}; run update")
        if row["decision"] == "exclude":
            for field in ("class", "state", "scale"):
                if row[field]:
                    raise MappingError(f"{where}: exclude requires empty {field}")
            continue
        material = row["class"]
        if material not in TILE_METERS:
            raise MappingError(f"{where}: unknown class {material!r}; expected one of {', '.join(TILE_METERS)}")
        if not STATE.fullmatch(row["state"]):
            raise MappingError(f"{where}: state must contain 2–5 English words")
        if Decimal(source["tiled_area_fraction"]) < MIN_TILED_FRACTION:
            raise MappingError(f"{where}: include requires tiled_area_fraction >= 0.6")
        scale = _number(row["scale"], f"{where}: scale", positive=True)
        expected = material_scale(Decimal(source["uv_m_per_unit_median"]), material)
        if scale != expected:
            raise MappingError(f"{where}: stale scale: {row['scale']!r}; expected {expected:.2f}; run update")


def check_mapping(mapping: Path = DEFAULT_MAPPING, census_path: Path = DEFAULT_CENSUS) -> list[dict[str, str]]:
    census = read_census(census_path)
    rows = _read_csv(mapping, mapping=True)
    validate_mapping(rows, census, path=mapping)
    return rows


def update_mapping(mapping: Path = DEFAULT_MAPPING, census_path: Path = DEFAULT_CENSUS) -> list[dict[str, str]]:
    census = read_census(census_path)
    rows = _read_csv(mapping, mapping=True)
    _check_coverage(rows, census, mapping)
    for row in rows:
        source = census[row["texture"]]
        for field in ("area_m2", "uv_m_per_unit_median"):
            row[field] = source[field]
        if row["decision"] == "include" and row["class"] in TILE_METERS:
            row["scale"] = f"{material_scale(Decimal(source['uv_m_per_unit_median']), row['class']):.2f}"
    validate_mapping(rows, census, path=mapping)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=mapping.parent, delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, mapping)
    except OSError as error:
        raise MappingError(f"{mapping}: {error}") from error
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return rows


def _summary(rows: list[dict[str, str]]) -> str:
    included = [row for row in rows if row["decision"] == "include"]
    area = sum((Decimal(row["area_m2"]) for row in included), Decimal(0))
    total = sum((Decimal(row["area_m2"]) for row in rows), Decimal(0))
    share = area / total * 100 if total else Decimal(0)
    return (
        f"rows={len(rows)} include={len(included)} exclude={len(rows) - len(included)}; "
        f"include area={area:.2f} / {total:.2f} m2 ({share:.2f}%)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "update"))
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    args = parser.parse_args(argv)
    try:
        action = check_mapping if args.command == "check" else update_mapping
        rows = action(args.mapping, args.census)
    except (MappingError, InvalidOperation) as error:
        print(f"Material mapping {args.command} failed: {error}", file=sys.stderr)
        return 1
    print(f"Material mapping {args.command} passed: {_summary(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
