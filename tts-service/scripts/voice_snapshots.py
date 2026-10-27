"""Voice snapshots of plot npc's from the game's own Russian speech: ~20-25 s of calm lines per character,
for Inworld voice cloning. In the game only: shorts keep designed voices (the user, 2026-10-27).

A snapshot is the character's own lines (its dialogs, its voice index), neither shouted nor whispered,
3-6 s phrases from different dialogs first, evened out and joined with short pauses. Cutscenes are left
out: they carry music and reverb. The snapshots are the game's audio, so they stay outside the repository.

usage: uv run python scripts/voice_snapshots.py <game dir> [--ou ou.json] [--out DIR] [ID ...]
  --ou: the storygraph tool's `--ou-messages` output (it is run when omitted, ~2 min)
  --out: default ~/.local/share/gothic-ai/voices/original (WAVs and manifest.json)
"""

import argparse
import collections
import glob
import json
import re
import struct
import subprocess
from pathlib import Path

import numpy as np

RATE = 44100
REPO = Path(__file__).resolve().parents[2]
STORYGRAPH = REPO / "OpenGothic" / "ai_bridge" / "storygraph" / "build" / "storygraph"
DEFAULT_OUT = Path.home() / ".local/share/gothic-ai/voices/original"
TARGET_S = (20.0, 28.0)
GAP_S = 0.35

# id (primary instance) -> name, dialog tokens (first part of a line name after DIA_/ADDON_), other instances
CHARACTERS = {
    "NONE_100_XARDAS": ("Ксардас", ["XARDAS"], []),
    "VLK_439_VATRAS": ("Ватрас", ["VATRAS"], ["VLK_439_VATRAS_DI"]),
    "BAU_4300_ADDON_CAVALORN": ("Кавалорн", ["CAVALORN"], []),
    "PAL_200_HAGEN": ("Лорд Хаген", ["LORD_HAGEN", "HAGEN"], []),
    "PC_PSIONIC": ("Лестер", ["LESTER", "PC_LESTER"], ["PC_PSIONIC_DI"]),
    "PC_THIEF_NW": ("Диего", ["DIEGO", "DIEGOOW", "DIEGONW", "DIEGODI"], ["PC_THIEFOW", "PC_THIEF_DI"]),
    "PC_MAGE_NW": ("Милтен", ["MILTEN", "MILTENOW", "MILTENNW"], ["PC_MAGE_OW", "PC_MAGE_DI"]),
    "PC_FIGHTER_NW_VOR_DJG": (
        "Горн",
        ["GORN", "GORNOW", "GORNNW", "GORNDJG"],
        ["PC_FIGHTER_OW", "PC_FIGHTER_DJG", "PC_FIGHTER_NW_NACH_DJG", "PC_FIGHTER_DI"],
    ),
    "SLD_800_LEE": ("Ли", ["LEE"], ["SLD_800_LEE_DI"]),
    "VLK_449_LARES": ("Ларес", ["LARES"], ["VLK_449_LARES_DI"]),
    "KDF_500_PYROKAR": ("Пирокар", ["PYROKAR"], []),
    "KDF_501_SERPENTES": ("Серпентес", ["SERPENTES"], []),
    "KDF_502_ULTHAR": ("Ультар", ["ULTHAR"], []),
    "PAL_203_LOTHAR": ("Лотар", ["LOTHAR"], []),
    "PAL_250_GAROND": ("Гаронд", ["GAROND"], []),
    "PAL_252_PARCIVAL": ("Парцифаль", ["PARCIVAL"], []),
    "MIL_311_ANDRE": ("Лорд Андрэ", ["ANDRE"], []),
    "BAU_900_ONAR": ("Онар", ["ONAR"], []),
    "SLD_801_TORLOF": ("Торлоф", ["TORLOF"], ["SLD_801_TORLOF_DI"]),
    "SLD_809_BENNET": ("Беннет", ["BENNET"], ["SLD_809_BENNET_DI"]),
    "SLD_806_SYLVIO": ("Сильвио", ["SYLVIO", "SYLVIODJG"], ["DJG_700_SYLVIO"]),
    "SLD_811_WOLF": ("Вольф", ["WOLF"], ["SLD_811_WOLF_DI"]),
    "VLK_444_JACK": ("Джек", ["JACK"], ["VLK_444_JACK_DI"]),
    "DJG_705_ANGAR": ("Ангар", ["ANGAR", "ANGARDJG"], ["DJG_705_ANGAR_NW", "DJG_705_ANGAR_DI"]),
    "DJG_713_BIFF": ("Бифф", ["BIFF"], ["DJG_713_BIFF_NW", "DJG_713_BIFF_DI"]),
    "NONE_110_URSHAK": ("Ур-Шак", ["URSHAK"], []),
    "KDW_1400_ADDON_SATURAS_NW": ("Сатурас", ["SATURAS"], ["KDW_14000_ADDON_SATURAS_ADW"]),
    "KDW_1401_ADDON_CRONOS_NW": ("Кронос", ["CRONOS"], ["KDW_14010_ADDON_CRONOS_ADW"]),
    "KDW_1402_ADDON_NEFARIUS_NW": ("Нефариус", ["NEFARIUS"], ["KDW_14020_ADDON_NEFARIUS_ADW"]),
    "KDW_1403_ADDON_MYXIR_NW": ("Миксир", ["MYXIR"], ["KDW_14030_ADDON_MYXIR_ADW"]),
    "KDW_1404_ADDON_RIORDIAN_NW": ("Риордиан", ["RIORDIAN"], ["KDW_14040_ADDON_RIORDIAN_ADW"]),
    "KDW_1405_ADDON_MERDARION_NW": ("Мердарион", ["MERDARION"], ["KDW_14050_ADDON_MERDARION_ADW"]),
    "PIR_1320_ADDON_GREG": ("Грег", ["GREG"], ["PIR_1300_ADDON_GREG_NW"]),
    "BDT_1090_ADDON_RAVEN": ("Ворон", ["RAVEN"], []),
    "BDT_10014_ADDON_THORUS": ("Торус", ["THORUS"], ["VLK_2000_THORUS"]),
    "BDT_1083_ADDON_ESTEBAN": ("Эстебан", ["ESTEBAN"], []),
}
PREFIXES = ("DIA_ADDON_", "DIA_")  # cutscenes (INTRO_/OUTRO_) carry music and reverb
LINE = re.compile(r"(.*)_(\d+)_(\d+)$")


def speech_index(game: Path) -> dict[str, tuple[str, int, int, int]]:
    """Line name -> (vdf, offset, size, timestamp); the newest archive wins, like the engine's mount order."""
    out: dict[str, tuple[str, int, int, int]] = {}
    for f in sorted(glob.glob(str(game / "Data" / "*peech*.vdf"))):
        if "engl" in f.lower():
            continue
        with open(f, "rb") as d:
            n, _, ts, _, root, _ = struct.unpack("<6I", d.read(296)[272:296])
            d.seek(root)
            for _ in range(n):
                e = d.read(80)
                off, size, typ, _ = struct.unpack("<4I", e[64:])
                if typ & 0x80000000:
                    continue
                name = e[:64].rstrip(b" \0").decode("cp1251").upper()
                if not name.endswith(".WAV"):
                    continue
                key = name[:-4]
                if key not in out or out[key][3] <= ts:
                    out[key] = (f, off, size, ts)
    return out


def own_lines(names, tokens: list[str]) -> list[tuple[str, str]]:
    """(line name, voice) of the character's own dialogs, its own voice only (the majority there)."""
    found = []
    for name in names:
        m = LINE.match(name)
        if not m:
            continue
        rest = m[1]
        for p in PREFIXES:
            if rest.startswith(p):
                rest = rest[len(p) :]
                break
        if rest != m[1] and any(rest == t or rest.startswith(t + "_") for t in tokens):
            found.append((name, m[2]))
    voices = collections.Counter(v for _, v in found if v != "15")
    if not voices:
        return []
    voice = voices.most_common(1)[0][0]
    return [(n, v) for n, v in found if v == voice]


def decode(vdf: str, off: int, size: int) -> np.ndarray:
    with open(vdf, "rb") as d:
        d.seek(off)
        raw = d.read(size)
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(RATE), "pipe:1"],
        input=raw,
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768


def trim(x: np.ndarray, floor_db: float = -45) -> np.ndarray:
    win = int(0.01 * RATE)
    if len(x) < win * 3:
        return x
    frames = x[: len(x) // win * win].reshape(-1, win)
    db = 20 * np.log10(np.sqrt((frames**2).mean(1)) + 1e-9)
    loud = np.nonzero(db > floor_db)[0]
    if not len(loud):
        return x[:0]
    a, b = max(0, loud[0] - 3), min(len(frames), loud[-1] + 6)  # 30 ms before, 60 ms tail
    return x[a * win : b * win]


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt((x**2).mean()) + 1e-9))


def build(cid: str, index, texts, out: Path) -> dict:
    name, tokens, aliases = CHARACTERS[cid]
    lines = own_lines(index.keys(), tokens)
    cands = []
    for line, voice in lines:
        text = texts.get(line, "")
        if not text:
            continue
        audio = trim(decode(*index[line][:3]))
        dur = len(audio) / RATE
        if 1.2 <= dur <= 12.0:
            cands.append(
                {"line": line, "text": text, "s": dur, "db": rms_db(audio), "audio": audio, "voice": voice}
            )
    if not cands:
        return {"id": cid, "name": name, "error": "no lines"}
    med = float(np.median([c["db"] for c in cands]))
    # calm first: no shouts, short questions are mostly a tone, not the voice; then looser for the few-lined
    passes = [
        lambda c: (
            c["s"] <= 8
            and "!" not in c["text"]
            and not ("?" in c["text"] and len(c["text"]) < 25)
            and abs(c["db"] - med) <= 3
        ),
        lambda c: c["s"] <= 8 and "!" not in c["text"] and abs(c["db"] - med) <= 4.5,
        lambda c: abs(c["db"] - med) <= 6,
    ]
    picked, dialogs, total = [], set(), 0.0
    for last, ok in enumerate(passes, 1 - len(passes)):
        calm = sorted((c for c in cands if ok(c) and c not in picked), key=lambda c: abs(c["s"] - 4.5))
        for c in calm:  # 3-6 s phrases first: whole sentences, more of them per snapshot
            dialog = LINE.match(c["line"])[1]
            if (dialog in dialogs and last) or total + c["s"] > TARGET_S[1]:
                continue
            picked.append(c)
            dialogs.add(dialog)
            total += c["s"] + GAP_S
            if total >= TARGET_S[0]:
                break
        if total >= TARGET_S[0]:
            break
    calm = [c for c in cands if passes[0](c)]
    picked.sort(key=lambda c: c["line"])
    gap = np.zeros(int(GAP_S * RATE), dtype=np.float32)
    parts = []
    for c in picked:
        parts += [c["audio"] * 10 ** ((med - c["db"]) / 20), gap]  # even lines out to the voice's median
    audio = np.concatenate(parts[:-1])
    audio *= 10 ** ((-20.0 - rms_db(audio)) / 20)
    audio = np.clip(audio * min(1.0, 0.9 / max(1e-9, np.abs(audio).max())), -1, 1)
    wav = out / f"{cid}.wav"
    write_wav(wav, audio)
    return {
        "id": cid,
        "name": name,
        "aliases": aliases,
        "voice": picked[0]["voice"],
        "seconds": round(len(audio) / RATE, 1),
        "own_lines": len(lines),
        "calm_lines": len(calm),
        "file": str(wav),
        "lines": [{"line": c["line"], "text": c["text"], "s": round(c["s"], 2)} for c in picked],
    }


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (x * 32767).astype("<i2").tobytes()
    hdr = (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
    )
    path.write_bytes(hdr + b"data" + struct.pack("<I", len(pcm)) + pcm)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("game", type=Path)
    p.add_argument("--ou", type=Path)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("ids", nargs="*")
    args = p.parse_args()
    game, out = args.game, args.out
    only = args.ids or list(CHARACTERS)
    out.mkdir(parents=True, exist_ok=True)
    if args.ou:
        raw = args.ou.read_bytes()
    else:
        raw = subprocess.run(
            [str(STORYGRAPH), str(game), "--ou-messages"], capture_output=True, check=True
        ).stdout
    data = json.loads(raw)
    texts = {}
    for n, t in data["messages"]:
        n = n.encode("latin1").decode("ascii", "replace").upper()
        if n.endswith(".WAV"):
            texts[n[:-4]] = t.encode("latin1").decode("cp1251", "replace").strip()
    index = speech_index(game)
    path = out / "manifest.json"
    manifest = {r["id"]: r for r in (json.loads(path.read_text()) if path.exists() else [])}
    for cid in only:  # a run for a few ids keeps the others' entries
        r = manifest[cid] = build(cid, index, texts, out)
        lines = len(r.get("lines", []))
        print(
            f"{cid:30} {r['name']:12} {r.get('seconds', 0):5} s  {lines} lines  voice {r.get('voice')}",
            r.get("error", ""),
        )
    path.write_text(
        json.dumps([manifest[c] for c in CHARACTERS if c in manifest], ensure_ascii=False, indent=1)
    )


if __name__ == "__main__":
    main()
