"""Dialog line library (OU.BIN, ZenGin BinSafe archive): line name -> subtitle text.

In the archive every spoken line is a string entry with the subtitle, a hash entry and a string entry
with `<NAME>.WAV`; strings are `0x01 <u16 length> <cp1251 bytes>`, hashes `0x12 <u32>`.
Line names look like `DIA_<Npc>_<Info>_<voice>_<n>`; voice 15 is the hero.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

HERO_VOICE = "15"
_WAV_ENTRY = re.compile(rb"\x01(..)([A-Za-z0-9_]+)\.WAV", re.DOTALL)
_MAX_TEXT = 4096


def find_ou_file(game_dir: Path) -> Path | None:
    cutscene = game_dir / "_work" / "Data" / "Scripts" / "CONTENT" / "CUTSCENE"
    if not cutscene.is_dir():
        return None
    for entry in cutscene.iterdir():
        if entry.name.upper() == "OU.BIN":
            return entry
    return None


def parse_ou_bin(data: bytes, encoding: str = "cp1251") -> dict[str, str]:
    lines: dict[str, str] = {}
    for m in _WAV_ENTRY.finditer(data):
        name = m.group(2).decode("ascii")
        if int.from_bytes(m.group(1), "little") != len(name) + 4:
            continue
        hash_pos = m.start() - 5
        if hash_pos < 0 or data[hash_pos] != 0x12:
            continue
        text = _string_ending_at(data, hash_pos)
        if text is not None:
            lines[name.upper()] = text.decode(encoding, "replace").strip()
    return lines


def _string_ending_at(data: bytes, end: int) -> bytes | None:
    for start in range(end - 3, max(end - 3 - _MAX_TEXT, -1), -1):
        if data[start] == 0x01 and int.from_bytes(data[start + 1 : start + 3], "little") == end - start - 3:
            return data[start + 3 : end]
    return None


def npc_dialog_key(symbol: str) -> str:
    """`NONE_100_XARDAS` -> `XARDAS`, `VLK_408_JORA` -> `JORA`: the part after the numeric id."""
    parts = symbol.upper().split("_")
    for i, part in enumerate(parts):
        if part.isdigit():
            return "_".join(parts[i + 1 :])
    return parts[-1]


def npc_lines(lines: dict[str, str], symbol: str) -> list[tuple[str, str]]:
    """Lines spoken by the npc itself in its own dialogs (hero lines excluded), sorted by name."""
    prefix = f"DIA_{npc_dialog_key(symbol)}_"
    out = []
    for name, text in sorted(lines.items()):
        parts = name.split("_")
        if name.startswith(prefix) and len(parts) >= 4 and parts[-2] != HERO_VOICE and text:
            out.append((name, text))
    return out


def voice_index(lines: Iterable[tuple[str, str]]) -> str | None:
    """The original voice actor of an npc's lines (`npc_lines`): the most common `<voice>` in their names.
    The npc's own dialogs may have a line of someone else's now and then, hence the majority."""
    voices = Counter(p[-2] for p in (name.split("_") for name, _ in lines) if len(p) >= 4 and p[-2].isdigit())
    return voices.most_common(1)[0][0] if voices else None


def load_npc_lines(game_dir: Path, symbol: str) -> list[tuple[str, str]]:
    path = find_ou_file(game_dir)
    if path is None:
        return []
    return npc_lines(parse_ou_bin(path.read_bytes()), symbol)
