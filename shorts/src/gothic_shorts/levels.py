"""Deterministic, pre-master balance for the before/after shorts.

Every measurement taps the very same stem graph used by the mix. Word timings,
not signal dynamics, control ducking. All times refer to the joined video.
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MUSIC_BELOW_VOICE_LU = 14.0
MUSIC_DUCK_DB = 6.0
GAME_BELOW_VOICE_LU = 6.0
LEVEL_TOLERANCE_LU = 1.5
RAMP_SECONDS = 0.150
# ebur128's summary is rounded to tenths; keep game gain on the safe side.
METER_RESOLUTION_LU = 0.1

Window = tuple[float, float]


def _merge(windows: list[Window], duration: float) -> list[Window]:
    merged: list[Window] = []
    for start, end in sorted(windows):
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def speech_windows(plan: dict) -> tuple[list[Window], float]:
    """First to last word of each line, clipped to the body (never the sting)."""
    windows, offset = [], 0.0
    for beat in plan["beats"]:
        for line in beat.get("lines", []):
            words = line["words"]
            if not words:
                raise ValueError("A voiced line needs word timings")
            at = offset + line["at"] - beat["start"]
            start, end = at + words[0][1], at + words[-1][2]
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                raise ValueError("Invalid speech window")
            windows.append((start, end))
        offset += beat["end"] - beat["start"]
    return _merge(windows, offset), offset


def pause_windows(speech: list[Window], duration: float) -> list[Window]:
    pauses, cursor = [], 0.0
    for start, end in _merge(speech, duration):
        if start > cursor:
            pauses.append((cursor, start))
        cursor = end
    if cursor < duration:
        pauses.append((cursor, duration))
    return pauses


def duck_filter(windows: list[Window], attenuation_db: float) -> str:
    """Full attenuation throughout each word window, ramps in the adjacent pauses.

    The maximum of overlapping ramps avoids a gain jump between nearby lines.
    Fixed 1-ms audio frames bound timing error independently of input packet sizes.
    """
    if not windows or attenuation_db <= 0:
        return "anull"
    ramps = [
        f"max(0,min(1,min((t-({start:.9f})+{RAMP_SECONDS})/{RAMP_SECONDS},"
        f"(({end:.9f})+{RAMP_SECONDS}-t)/{RAMP_SECONDS})))"
        for start, end in windows
    ]
    envelope = ramps[0]
    for ramp in ramps[1:]:
        envelope = f"max({envelope},{ramp})"
    # Linear amplitude ramps; the plateau is exactly the requested dB reduction.
    gain = 10 ** (-attenuation_db / 20)
    return (f"aresample=48000,asetnsamples=n=48:p=0,"
            f"volume='1-({1 - gain:.12f})*({envelope})':eval=frame")


def measure(inputs: list[str], graph: str, output: str,
            windows: list[Window] | None = None) -> float:
    """EBU R128 integrated LUFS after exact sample trims, before any mastering.

    Other outputs of graph must already be consumed. Disjoint windows are joined
    without artificial silence. Empty/silent selections return -inf; selections
    shorter than an R128 block are rejected rather than silently passing a check.
    """
    if windows is not None:
        if not windows:
            return -math.inf
        if sum(end - start for start, end in windows) < 0.4 - 1e-6:
            raise ValueError("At least 400 ms of audio is needed for an EBU R128 measurement")
        branches = "".join(f"[level_split{i}]" for i in range(len(windows)))
        graph += f";[{output}]asplit={len(windows)}{branches}"
        for i, (start, end) in enumerate(windows):
            graph += (f";[level_split{i}]atrim=start={start:.9f}:end={end:.9f},"
                      f"asetpts=PTS-STARTPTS[level_piece{i}]")
        graph += ";" + "".join(f"[level_piece{i}]" for i in range(len(windows)))
        graph += f"concat=n={len(windows)}:v=0:a=1[level_selection]"
        output = "level_selection"
    graph += f";[{output}]ebur128=peak=true[level_meter]"
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", *inputs, "-filter_complex", graph,
         "-map", "[level_meter]", "-vn", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )
    readings = re.findall(r"\bI:\s*(-?\d+(?:\.\d+)?|[-+]?inf)\s+LUFS", result.stderr)
    if not readings:
        raise ValueError("ffmpeg did not return an EBU R128 integrated loudness")
    value = float(readings[-1])
    return value if value > -70 and math.isfinite(value) else -math.inf


def music_gain(speech_lufs: float, music_pause_lufs: float) -> float:
    if not math.isfinite(speech_lufs) or not math.isfinite(music_pause_lufs):
        raise ValueError("Voice and music pauses must have measurable EBU R128 loudness")
    return speech_lufs - MUSIC_BELOW_VOICE_LU - music_pause_lufs


def check_balance(speech_lufs: float | None, music_pause_lufs: float | None,
                  music_speech_lufs: float | None, game_speech_lufs: float | None,
                  game_pause_lufs: float | None) -> dict:
    """The table is also the machine-readable publication gate; None means absent."""
    voiced = speech_lufs is not None
    rows = []

    def row(key: str, value: float | None, target: float | None, description: str,
            ok: bool | None) -> None:
        rows.append({"id": key, "measured_lufs": value if value is not None and math.isfinite(value)
                     else None, "target_lufs": target, "target": description, "ok": ok})

    row("voice", speech_lufs, None, "S (reference)",
        math.isfinite(speech_lufs) if voiced else None)
    for key, value, below in (("music_pause", music_pause_lufs, MUSIC_BELOW_VOICE_LU),
                              ("music_speech", music_speech_lufs,
                               MUSIC_BELOW_VOICE_LU + MUSIC_DUCK_DB)):
        target = speech_lufs - below if voiced and math.isfinite(speech_lufs) else None
        ok = (math.isfinite(value) and abs(value - target) <= LEVEL_TOLERANCE_LU + 1e-6
              if value is not None and target is not None else None)
        row(key, value, target, f"S - {below:g} LU ± {LEVEL_TOLERANCE_LU:g}" if voiced else "measured", ok)
    target = speech_lufs - GAME_BELOW_VOICE_LU if voiced and math.isfinite(speech_lufs) else None
    row("game_speech", game_speech_lufs, target,
        f"≤ S - {GAME_BELOW_VOICE_LU:g} LU" if voiced else "measured",
        game_speech_lufs <= target + 1e-6 if game_speech_lufs is not None and target is not None else None)
    row("game_pause", game_pause_lufs, None,
        f"original (except {RAMP_SECONDS * 1000:g} ms ramps)" if voiced else "measured",
        True if voiced and game_pause_lufs is not None else None)
    return {"voiced": voiced, "ok": all(r["ok"] is not False for r in rows), "rows": rows}


def print_balance(report: dict) -> None:
    print(f"{'Track / window':<20} {'LUFS':>8} {'Target LUFS':>12}  {'Target':<31} Result")
    for row in report["rows"]:
        value = "—" if row["measured_lufs"] is None else f"{row['measured_lufs']:.1f}"
        target = "—" if row["target_lufs"] is None else f"{row['target_lufs']:.1f}"
        result = "ок" if row["ok"] else "нет" if row["ok"] is False else "—"
        print(f"{row['id']:<20} {value:>8} {target:>12}  {row['target']:<31} {result}")


def linear_master_filter(measured: dict) -> str:
    """Choose a feasible loudnorm target so FFmpeg cannot need dynamic limiting.

    A high crest factor may require an output below -14 LUFS to retain both the
    balance and the -1.5 dBTP ceiling. A tenth of a dB covers rounded probe data.
    """
    i, peak, lra = (float(measured[key]) for key in ("input_i", "input_tp", "input_lra"))
    if not all(math.isfinite(value) for value in (i, peak, lra)):
        raise ValueError("The mix has no measurable loudness for linear mastering")
    target_i = min(-14.0, i - 1.5 - peak - METER_RESOLUTION_LU)
    target_lra = max(11.0, lra + METER_RESOLUTION_LU)
    if target_i < -70 or target_lra > 50:
        raise ValueError("The mix is outside loudnorm's linear mastering range")
    # FFmpeg treats measured_LRA=0 as an absent measurement and forces dynamic
    # mode. Use the smallest positive value at the probe's 0.01-LU precision;
    # LRA only gates linear mode, so this does not change the common gain.
    measured_lra = measured["input_lra"] if lra else "0.01"
    return (f"loudnorm=I={target_i:.3f}:TP=-1.5:LRA={target_lra:.3f}"
            f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured_lra}:measured_thresh={measured['input_thresh']}"
            ":offset=0:linear=true:print_format=json")


@dataclass
class AudioGraph:
    inputs: list[str]
    filters: list[str]
    tracks: dict[str, str]

    def measure(self, track: str, windows: list[Window] | None = None) -> float:
        graph = self.filters + [f"[{label}]anullsink" for key, label in self.tracks.items() if key != track]
        return measure(self.inputs, ";".join(graph), self.tracks[track], windows)

    def mix(self) -> str:
        return ";".join(self.filters + ["".join(f"[{label}]" for label in self.tracks.values()) +
                                       f"amix=inputs={len(self.tracks)}:duration=first:normalize=0"])


def audio_graph(joined: Path, music: Path | None, voice: list[tuple[Path, float]],
                speech: list[Window], total: float, length: float, *,
                music_volume: float = 1.0, music_gain_db: float = 0.0,
                game_duck_db: float = 0.0) -> AudioGraph:
    """One source of truth for the mix and all calibration/validation taps."""
    inputs, filters, tracks = ["-i", str(joined)], [], {"game": "g"}
    if music is not None:
        inputs += ["-i", str(music)]
    if voice:
        filters.append(f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                       f"atrim=duration={length},{duck_filter(speech, game_duck_db)}[g]")
        first = len(inputs) // 2
        for k, (wav, at) in enumerate(voice):
            inputs += ["-i", str(wav)]
            trim = f"atrim=start={-at},asetpts=PTS-STARTPTS," if at < 0 else ""
            ms = max(0, round(at * 1000))
            filters.append(f"[{first + k}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                           f"{trim}adelay={ms}|{ms}[v{k}]")
        filters.append("".join(f"[v{k}]" for k in range(len(voice))) +
                       f"amix=inputs={len(voice)}:duration=longest:normalize=0,"
                       f"apad=whole_dur={length},atrim=duration={length}[vo]")
        tracks["voice"] = "vo"
    else:
        # Preserve the original, unvoiced filter graph and gains.
        filters.append("[0:a]anull[g]")
    if music is not None:
        volume = f"{music_gain_db:.9f}dB" if voice else str(music_volume)
        format_audio = "aformat=sample_rates=48000:channel_layouts=stereo," if voice else ""
        filters.append(f"[1:a]{format_audio}atrim=0:{total},asetpts=PTS-STARTPTS,"
                       f"volume={volume},afade=t=in:d=1,"
                       f"afade=t=out:st={total - 1.5}:d=1.5" +
                       (f",{duck_filter(speech, MUSIC_DUCK_DB)}" if voice else "") + "[m]")
        tracks["music"] = "m"
    return AudioGraph(inputs, filters, tracks)


def calibrate(joined: Path, music: Path | None, voice: list[tuple[Path, float]],
              speech: list[Window], total: float, length: float, *,
              music_volume: float = 1.0) -> tuple[AudioGraph, dict]:
    pauses = pause_windows(speech, total)
    raw = audio_graph(joined, music, voice, speech, total, length, music_volume=music_volume)
    if not voice:
        game = raw.measure("game")
        music_level = raw.measure("music") if music is not None else None
        return raw, check_balance(None, music_level, None, None, game)
    if not speech:
        raise ValueError("The voiced plan has no speech windows inside the video")
    s = raw.measure("voice")
    if not math.isfinite(s):
        raise ValueError("The voice track has no measurable EBU R128 loudness")
    music_pause = raw.measure("music", pauses) if music is not None else None
    music_db = music_gain(s, music_pause) if music_pause is not None and math.isfinite(music_pause) else 0.0
    game_speech = raw.measure("game", speech)
    game_db = max(0.0, game_speech - (s - GAME_BELOW_VOICE_LU))
    if game_db > 0:
        game_db += METER_RESOLUTION_LU
    final = audio_graph(joined, music, voice, speech, total, length,
                        music_gain_db=music_db, game_duck_db=game_db)
    report = check_balance(
        s, final.measure("music", pauses) if music is not None else None,
        final.measure("music", speech) if music is not None else None,
        final.measure("game", speech), final.measure("game", pauses) if pauses else None,
    )
    report.update({"music_gain_db": music_db, "game_duck_db": game_db,
                   "speech_windows": speech, "pause_windows": pauses})
    return final, report
