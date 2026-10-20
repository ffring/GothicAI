#!/usr/bin/env python3
"""Validate seven pilot captures or compose the four-variant review boards."""
import json
from pathlib import Path
import re
import sys

SPOTS = ("roof", "upper", "house", "interior", "facade", "quay", "stairs")
MATERIALS = (
    "NW_CITY_ROOF_01", "NW_CITY_PFLASTERSTEIN_01", "NW_CITY_BODENDIELEN_01",
    "NW_CITY_ROOF_INSIDE_01", "NW_CITY_WALL_03", "NW_CITY_HAFENKAI_BODEN_01",
    "NW_HARBOUR_STAIRS_01",
)


def texture_key(name):
    name = name.replace("\\", "/").rsplit("/", 1)[-1].upper()
    if name.endswith(".TGA"):
        return name[:-4]
    if name.endswith(".TEX"):
        return name[:-6] if name.endswith("-C.TEX") else name[:-4]
    return name


def check(directory, scenario, material_index):
    def require(condition, reason):
        if not condition:
            raise SystemExit(f"pilot capture {directory.name}: {reason}")
    require((directory / "exit.txt").read_text().strip() == "0", "nonzero game exit")
    log = (directory / "game.log").read_text(errors="replace")
    require("[bench] done" in log, "scenario did not finish")
    require(not re.search(r"materials: (invalid|unsupported|unable)|\[bench\] (can not|unknown|marvin failed|goto failed)", log), "game or material error")
    commands = [line.split("#", 1)[0].strip() for line in scenario.read_text().splitlines()]
    shots = [command[5:] for command in commands if command.startswith("shot ")]
    require(tuple(shots) == SPOTS, "unexpected shot list")
    for command in commands:
        if command.startswith(("goto ", "shot ")):
            require(f"[bench] {command}\n" in log, f"command not reached: {command}")
    for spot in SPOTS:
        image = directory / (spot + ".png")
        require(image.is_file() and image.stat().st_size > 0, f"missing shot: {spot}")
    index = json.loads(material_index.read_text())
    require(bool(re.search(rf"materials: {len(index)} entries from ", log)), "wrong material count")
    census = json.loads((directory / "census.json").read_text())
    opened = {texture_key(row["name"]) for row in census["files"] if row["kind"] == "texture"}
    replaced = {texture_key(row["name"]) for row in census["replacements"] if row["kind"] == "texture"}
    targets = set(MATERIALS)
    if index:
        require(targets <= replaced, "pilot textures not replaced: " + ", ".join(sorted(targets - replaced)))
        require(not (targets & opened), "pilot originals opened: " + ", ".join(sorted(targets & opened)))
    else:
        require(targets <= opened, "pilot originals not opened: " + ", ".join(sorted(targets - opened)))
        require(not (targets & replaced), "baseline replaces pilot materials")
    events = directory / "events.jsonl"
    for line in events.read_text().splitlines() if events.exists() else ():
        event = json.loads(line)
        kind = event.get("kind", "")
        require(not (kind == "error" or kind.endswith("_error") or event.get("pass") is False), f"event failure: {kind}")
    print(f"{directory.name}: shots={len(shots)} materials={len(index)} pilot_replaced={len(targets & replaced)} pilot_originals={len(targets & opened)}")


def compose(captures, output, iteration2=False):
    from PIL import Image, ImageDraw, ImageFont
    output.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default(size=22)
    variants = ("original", "city-3", "pilot-b", "pilot-c")
    labels = ("original", "A: city-3", "B: new prompt + colour", "C: B + Marigold + colour")
    if iteration2:
        variants = ("original", "city-3", "pilot-b", "pilot-2")
        labels = ("original", "city-3", "iteration 1 B", "iteration 2")
    for spot in SPOTS:
        panels = []
        for variant in variants:
            with Image.open(captures / variant / (spot + ".png")) as source:
                panel = source.convert("RGB")
                panel.thumbnail((768, 432), Image.Resampling.LANCZOS)
                panels.append(panel)
        width = max(panel.width for panel in panels)
        height = max(panel.height for panel in panels)
        board = Image.new("RGB", (width * 4, height + 44), "#17191b")
        draw = ImageDraw.Draw(board)
        for position, (label, panel) in enumerate(zip(labels, panels)):
            board.paste(panel, (position * width, 44))
            draw.text((position * width + 12, 10), f"{spot} | {label}", font=font, fill="white")
        board.save(output / f"{spot}.jpg", quality=90, optimize=True)
    print(f"composed {len(SPOTS)} {'/'.join(variants)} boards: {output}")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "check":
        check(*map(Path, sys.argv[2:]))
    elif len(sys.argv) == 4 and sys.argv[1] in ("compose", "compose-2"):
        compose(*map(Path, sys.argv[2:]), iteration2=sys.argv[1] == "compose-2")
    else:
        raise SystemExit("usage: capture-quality-pilot.py check DIRECTORY SCENARIO INDEX | compose|compose-2 CAPTURES OUTPUT")
