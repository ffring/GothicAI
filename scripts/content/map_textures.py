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

from pilot import selected_rows


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING = ROOT / "data/content/city-materials.csv"
DEFAULT_CENSUS = ROOT / "reports/content/2026-10-world-textures/newworld.csv"
FIELDS = (
    "texture", "decision", "class", "state", "scale", "area_m2",
    "uv_m_per_unit_median", "reason",
    "object", "role", "depicts", "colour", "weathering", "orientation",
    "layout", "layout_share", "anti_repeat", "target_luminance",
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
PANEL_CLASSES = frozenset({
    "plaster_panel", "masonry_panel", "timber_frame_panel", "planks_panel",
    "roof_panel", "stone_panel", "painted_panel", "iron_panel", "cloth_panel",
    "foliage_panel", "net_panel", "rope_panel", "ground_panel",
})
STRIP_CLASSES = frozenset({
    "timber_strip", "stone_strip", "roof_ridge_strip", "plaster_strip",
    "rope_strip", "iron_strip", "net_strip",
})
MODULE_CLASSES = frozenset({
    "timber_frame_tile", "framed_masonry_tile", "framed_boards_tile",
    "window_wall_tile", "quay_wall_tile", "roof_structure_tile", "stair_tile",
    "pavement_border_tile", "bound_timber_tile", "path_tile", "paving_transition_tile", "banded_metal_tile",
})
LAYOUTS = ("tiled", "panel", "strip", "atlas")
LAYOUT_DECISIONS = dict(zip(LAYOUTS, ("include", "panel", "strip", "exclude")))
DECISION_CLASSES = {
    "include": TILE_METERS, "panel": PANEL_CLASSES, "strip": STRIP_CLASSES,
    "module": MODULE_CLASSES,
}
DECISIONS = (*DECISION_CLASSES, "exclude")
SEMANTIC_FIELDS = ("object", "role", "depicts", "colour", "weathering", "orientation")
SHARE_TOLERANCE = Decimal("0.00001")
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


def layout_shares(value: str, *, where: str) -> dict[str, Decimal]:
    """Read all four area shares; the mapping stores their measured mixture."""
    shares = {}
    for item in value.split(";"):
        pair = item.split(":")
        if len(pair) != 2 or pair[0] not in LAYOUTS or pair[0] in shares:
            raise MappingError(f"{where}: layout_share must contain each of {', '.join(LAYOUTS)} once")
        layout, raw = pair
        share = _number(raw, f"{where}: layout_share {layout}")
        if share > 1:
            raise MappingError(f"{where}: layout_share {layout} must be at most 1")
        shares[layout] = share
    if set(shares) != set(LAYOUTS):
        raise MappingError(f"{where}: layout_share must contain each of {', '.join(LAYOUTS)} once")
    if abs(sum(shares.values()) - Decimal(1)) > SHARE_TOLERANCE:
        raise MappingError(f"{where}: layout_share must sum to 1 within {SHARE_TOLERANCE}")
    return shares


def _check_intent(row: dict[str, str], where: str) -> None:
    for field in SEMANTIC_FIELDS:
        value = row[field].strip()
        if not re.fullmatch(r"[ -~]+", value) or not re.search(r"[A-Za-z]", value):
            raise MappingError(f"{where}: {field} must contain nonempty English words")
    layout = row["layout"]
    if layout not in LAYOUTS:
        raise MappingError(f"{where}: layout must be one of {', '.join(LAYOUTS)}")
    shares = layout_shares(row["layout_share"], where=where)
    if shares[layout] != max(shares.values()):
        raise MappingError(f"{where}: layout must have the largest layout_share (ties allowed)")
    if row["decision"] == "module":
        if layout == "atlas":
            raise MappingError(f"{where}: atlas layout requires decision exclude")
    elif row["decision"] != "exclude" and row["decision"] != LAYOUT_DECISIONS[layout]:
        raise MappingError(f"{where}: {layout} layout requires decision {LAYOUT_DECISIONS[layout]}")
    if row["anti_repeat"] not in ("on", "off"):
        raise MappingError(f"{where}: anti_repeat must be on or off")
    if row["decision"] != "include" and row["anti_repeat"] != "off":
        raise MappingError(f"{where}: {row['decision']} requires anti_repeat=off")


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
    pilot = {row["texture"] for row in selected_rows(rows, "city-2")}
    for line, row in enumerate(rows, 2):
        texture = row["texture"]
        where = f"{path}:{line}: {texture}"
        target = row["target_luminance"]
        if target or texture in pilot:
            luminance = _number(target, f"{where}: target_luminance")
            if luminance > 1:
                raise MappingError(f"{where}: target_luminance must be at most 1")
        if row["decision"] not in DECISIONS:
            raise MappingError(f"{where}: decision must be {', '.join(DECISIONS)}")
        if not row["reason"].strip():
            raise MappingError(f"{where}: reason must be nonempty")
        _check_intent(row, where)
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
        classes = DECISION_CLASSES[row["decision"]]
        if material not in classes:
            raise MappingError(f"{where}: unknown class {material!r} for {row['decision']}; expected one of {', '.join(sorted(classes))}")
        if not STATE.fullmatch(row["state"]):
            raise MappingError(f"{where}: state must contain 2–5 English words")
        scale = _number(row["scale"], f"{where}: scale", positive=True)
        expected = material_scale(Decimal(source["uv_m_per_unit_median"]), material) if row["decision"] == "include" else Decimal(1)
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
        elif row["decision"] in ("panel", "strip", "module"):
            row["scale"] = "1.00"
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
    counts = " ".join(f"{decision}={sum(row['decision'] == decision for row in rows)}" for decision in DECISIONS)
    return f"rows={len(rows)} {counts}; include area={area:.2f} / {total:.2f} m2 ({share:.2f}%)"


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
