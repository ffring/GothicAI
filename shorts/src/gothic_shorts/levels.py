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

BASE_LUFS = -14.0
TRUE_PEAK_DBTP = -1.0
VOICE_TOLERANCE_LU = 0.5
MASTER_CORRECTION_TOLERANCE_LU = 0.1
MAX_MASTER_CORRECTIONS = 2
MAX_ENDING_CORRECTIONS = 2
CEILING_HEADROOM_LU = 0.5
PEAK_LIMIT = 0.79  # Leave AAC/intersample headroom below the final true-peak ceiling.
# A sub-threshold pilot follows the limiter's shared gain without influencing it.
LIMITER_PILOT = 0.01
MUSIC_BELOW_VOICE_LU = 14.0
MUSIC_BELOW_GAME_LU = 3.0
MUSIC_DUCK_DB = 6.0
GAME_BELOW_VOICE_LU = 6.0
GAME_PAUSE_BELOW_VOICE_LU = 3.0
ENDING_BELOW_VOICE_LU = 3.0
ENDING_CHECK_BELOW_VOICE_LU = 2.0
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


def body_gain_filter(gain_db: float, total: float, ending_gain_db: float = 0.0) -> str:
    """Lower gain in the body's fade; raise it only once the ending has begun.

    Raising gain during the body's 60 ms fade can overpower that fade and make
    the last effects louder than their calibrated level. Keep that body gain
    until the boundary, then ramp up over the ending's first 60 ms instead.
    """
    if gain_db == ending_gain_db:
        return "anull" if gain_db == 0 else f"volume={gain_db:.9f}dB"
    gain, ending = 10 ** (gain_db / 20), 10 ** (ending_gain_db / 20)
    start, end = ((total, total + 0.06) if ending_gain_db > gain_db
                  else (max(0.0, total - 0.06), total))
    return (f"aresample=48000,asetnsamples=n=48:p=0,"
            f"volume='if(lt(t,{start:.9f}),{gain:.12f},if(lt(t,{end:.9f}),"
            f"{gain:.12f}+({ending - gain:.12f})*(t-{start:.9f})/{end - start:.9f},"
            f"{ending:.12f}))':eval=frame")


def measure(inputs: list[str], graph: str, output: str,
            windows: list[Window] | None = None, *, meter_gain_db: float = 0.0) -> float:
    """EBU R128 integrated LUFS after exact sample trims, before any mastering.

    Other outputs of graph must already be consumed. Disjoint windows are joined
    without artificial silence. Empty/silent selections return -inf; selections
    shorter than an R128 block are rejected rather than silently passing a check.
    The common mastering gain usually clears R128's absolute gate. If it does
    not, normalize the selected signal's measured peak for one metering retry.
    Subtract every measurement-only gain to preserve the stem's actual level.
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

    def meter(gain_db: float) -> tuple[float, str]:
        gain = f"volume={gain_db:.9f}dB:precision=double," if gain_db else ""
        metering_graph = f"{graph};[{output}]{gain}ebur128=peak=true[level_meter]"
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", *inputs, "-filter_complex", metering_graph,
             "-map", "[level_meter]", "-vn", "-f", "null", "-"],
            check=True, capture_output=True, text=True,
        )
        readings = re.findall(r"\bI:\s*(-?\d+(?:\.\d+)?|[-+]?inf)\s+LUFS", result.stderr)
        if not readings:
            raise ValueError("ffmpeg did not return an EBU R128 integrated loudness")
        return float(readings[-1]), result.stderr

    value, diagnostic = meter(meter_gain_db)
    if value > -70 and math.isfinite(value):
        return value - meter_gain_db
    peaks = re.findall(r"\bPeak:\s*(-?\d+(?:\.\d+)?|[-+]?inf)\s+dBFS", diagnostic)
    peak = float(peaks[-1]) if peaks else -math.inf
    if not math.isfinite(peak):
        return -math.inf  # Actual silence cannot be made measurable by amplification.
    extra_gain = max(0.0, -10.0 - peak)
    if not extra_gain:
        return -math.inf
    gain = meter_gain_db + extra_gain
    value, _ = meter(gain)
    return value - gain if value > -70 and math.isfinite(value) else -math.inf


def music_target(speech_lufs: float, game_pause_lufs: float | None = None) -> float:
    if not math.isfinite(speech_lufs):
        raise ValueError("Voice must have measurable EBU R128 loudness")
    target = speech_lufs - MUSIC_BELOW_VOICE_LU
    return min(target, game_pause_lufs - MUSIC_BELOW_GAME_LU) if game_pause_lufs is not None else target


def music_gain(speech_lufs: float, music_pause_lufs: float,
               game_pause_lufs: float | None = None) -> float:
    if not math.isfinite(music_pause_lufs):
        raise ValueError("Music pauses must have measurable EBU R128 loudness")
    return music_target(speech_lufs, game_pause_lufs) - music_pause_lufs


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def check_balance(speech_lufs: float | None, music_pause_lufs: float | None,
                  music_speech_lufs: float | None, game_speech_lufs: float | None,
                  game_pause_lufs: float | None, ending_lufs: float | None = None) -> dict:
    """The table is also the machine-readable publication gate; None means absent."""
    voiced = speech_lufs is not None
    s = _finite(speech_lufs)
    rows = []

    def row(key: str, value: float | None, target: float | None, description: str,
            ok: bool | None) -> None:
        rows.append({"id": key, "measured_lufs": _finite(value), "target_lufs": _finite(target),
                     "target": description, "ok": ok})

    row("voice", speech_lufs, None, "S (reference)", s is not None if voiced else None)
    target = music_target(s, game_pause_lufs) if s is not None else None
    for key, value, duck in (("music_pause", music_pause_lufs, 0),
                             ("music_speech", music_speech_lufs, MUSIC_DUCK_DB)):
        goal = target - duck if target is not None else None
        ok = (value == goal or math.isfinite(value) and abs(value - goal) <= LEVEL_TOLERANCE_LU + 1e-6
              if value is not None and goal is not None else None)
        description = (f"min(S - 14, game_pause - 3) - {duck:g} LU ± {LEVEL_TOLERANCE_LU:g}"
                       if voiced else "measured")
        row(key, value, goal, description, ok)
    for key, value, below in (("game_speech", game_speech_lufs, GAME_BELOW_VOICE_LU),
                              ("game_pause", game_pause_lufs, GAME_PAUSE_BELOW_VOICE_LU),
                              ("ending", ending_lufs, ENDING_CHECK_BELOW_VOICE_LU)):
        goal = s - below if s is not None else None
        row(key, value, goal, f"≤ S - {below:g} LU" if voiced else "measured",
            value <= goal + 1e-6 if value is not None and goal is not None else None)
    for key, value, reference, name in (
        ("game_below_voice", game_pause_lufs, s, "voice"),
        ("music_below_game", music_pause_lufs, game_pause_lufs, "game_pause"),
    ):
        goal = reference - MUSIC_BELOW_GAME_LU if reference is not None else None
        row(key, value, goal, f"≤ {name} - 3 LU + {LEVEL_TOLERANCE_LU:g}" if voiced else "measured",
            value <= goal + LEVEL_TOLERANCE_LU + 1e-6
            if voiced and value is not None and goal is not None else None)
    return {"voiced": voiced, "ok": all(r["ok"] is not False for r in rows), "rows": rows}


def check_mastering(report: dict, voice_lufs: float, true_peak_dbtp: float,
                    click_times: list[float]) -> None:
    report["rows"].extend([
        {"id": "voice_mastered", "measured_lufs": _finite(voice_lufs), "target_lufs": BASE_LUFS,
         "target": f"{BASE_LUFS:g} LUFS ± {VOICE_TOLERANCE_LU:g}",
         "ok": math.isfinite(voice_lufs) and abs(voice_lufs - BASE_LUFS) <= VOICE_TOLERANCE_LU},
        {"id": "true_peak", "measured_dbtp": _finite(true_peak_dbtp), "target_dbtp": TRUE_PEAK_DBTP,
         "target": f"≤ {TRUE_PEAK_DBTP:g} dBTP (AAC)",
         "ok": math.isfinite(true_peak_dbtp) and true_peak_dbtp <= TRUE_PEAK_DBTP},
        {"id": "clicks", "count": len(click_times), "times_seconds": click_times,
         "target": "0 clicks (AAC)", "ok": not click_times},
    ])
    report["ok"] = all(row["ok"] is not False for row in report["rows"])


def check_mastered_balance(stems: dict[str, Path], speech_lufs: float,
                           speech: list[Window], pauses: list[Window], ending: list[Window]) -> dict:
    """Validate decoded AAC stems under the actual mix limiter, using the final voice as S."""
    def reading(track: str, windows: list[Window]) -> float | None:
        if track not in stems or not windows:
            return None
        return measure(["-i", str(stems[track])], "[0:a]anull[stem]", "stem", windows)

    return check_balance(speech_lufs, reading("music", pauses), reading("music", speech),
                         reading("game", speech), reading("game", pauses), reading("game", ending))


def print_balance(report: dict) -> None:
    print(f"{'Track / window':<20} {'Measured':>10} {'Target':>12}  Rule / result")
    for row in report["rows"]:
        value = row.get("measured_lufs", row.get("measured_dbtp", row.get("count")))
        target = row.get("target_lufs", row.get("target_dbtp"))
        precision = 6 if "measured_dbtp" in row else 1
        value = "—" if value is None else f"{value:.{precision}f}"
        target = "—" if target is None else f"{target:.1f}"
        result = "ок" if row["ok"] else "нет" if row["ok"] is False else "—"
        print(f"{row['id']:<20} {value:>10} {target:>12}  {row['target']}  {result}")
        if row.get("times_seconds"):
            print("  Clicks at (s): " + ", ".join(f"{at:.6f}" for at in row["times_seconds"]))


def master_gain(speech_lufs: float) -> float:
    if not math.isfinite(speech_lufs):
        raise ValueError("The voice has no measurable loudness for mastering")
    return BASE_LUFS - speech_lufs


def master_correction(voice_lufs: float) -> float:
    """Correct decoded voice loudness; leave an unavailable reading to the final gate."""
    if not math.isfinite(voice_lufs):
        return 0.0
    correction = BASE_LUFS - voice_lufs
    return correction if abs(correction) > MASTER_CORRECTION_TOLERANCE_LU + 1e-6 else 0.0


def ending_correction(voice_lufs: float, ending_lufs: float | None) -> float:
    """Recalibrate the ending after common gain corrections and actual voice limiting."""
    if ending_lufs is None or not all(math.isfinite(value) for value in (voice_lufs, ending_lufs)):
        return 0.0
    correction = voice_lufs - ENDING_BELOW_VOICE_LU - CEILING_HEADROOM_LU - ending_lufs
    return correction if abs(correction) > MASTER_CORRECTION_TOLERANCE_LU + 1e-6 else 0.0


def master_filter(speech_lufs: float, length: float, *, voice_probe: bool = False,
                  gain_correction_db: float = 0.0) -> str:
    pilot = f"aeval='val(0)|val(1)|{LIMITER_PILOT}':c=2.1," if voice_probe else ""
    return (f"volume={master_gain(speech_lufs) + gain_correction_db:.9f}dB,{pilot}"
            f"alimiter=limit={PEAK_LIMIT}:level=false:latency=true,"
            f"afade=t=out:st={length - 0.08:.3f}:d=0.08")


def meter_file(path: Path) -> tuple[float, float]:
    """Combine precise block peaks with the summary covering the final partial block."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
         "ebur128=peak=true:metadata=1,ametadata=print:key=lavfi.r128.true_peak",
         "-f", "null", "-"], check=True, capture_output=True, text=True,
    )
    loudness = re.findall(r"\bI:\s*(-?\d+(?:\.\d+)?|[-+]?inf)\s+LUFS", result.stderr)
    peaks = [float(value) for value in re.findall(r"\blavfi\.r128\.true_peak=([^\s]+)", result.stderr)]
    summaries = re.findall(r"\bPeak:\s*(-?\d+(?:\.\d+)?|[-+]?inf)\s+dBFS", result.stderr)
    if not loudness or not summaries:
        raise ValueError("ffmpeg did not return decoded loudness and true peak")
    if any(not math.isfinite(peak) or peak < 0 for peak in peaks):
        raise ValueError("ffmpeg returned invalid true-peak metadata")
    summary_peak = float(summaries[-1])
    if summary_peak == math.inf:
        raise ValueError("ffmpeg returned invalid true-peak summary")
    peak = max(peaks, default=0)
    metadata_peak = 20 * math.log10(peak) if peak > 0 else -math.inf
    return float(loudness[-1]), max(metadata_peak, summary_peak)


@dataclass
class AudioGraph:
    inputs: list[str]
    filters: list[str]
    tracks: dict[str, str]

    def measure(self, track: str, windows: list[Window] | None = None, *,
                meter_gain_db: float = 0.0) -> float:
        graph = self.filters + [f"[{label}]anullsink" for key, label in self.tracks.items() if key != track]
        return measure(self.inputs, ";".join(graph), self.tracks[track], windows,
                       meter_gain_db=meter_gain_db)

    def mix(self, *, stem_probes: bool = False, ending_gain_correction_db: float = 0.0,
            ending_start: float | None = None) -> str:
        filters, tracks = list(self.filters), dict(self.tracks)
        if ending_gain_correction_db:
            if ending_start is None:
                raise ValueError("An ending correction needs the body's duration")
            ending_filter = body_gain_filter(0.0, ending_start, ending_gain_correction_db)
            filters.append(f"[{tracks['game']}]{ending_filter}[ending_adjusted]")
            tracks["game"] = "ending_adjusted"
        if stem_probes:
            for track, label in tracks.items():
                filters.append(f"[{label}]asplit=2[master_{track}_mix][master_{track}_probe]")
                tracks[track] = f"master_{track}_mix"
        return ";".join(filters + ["".join(f"[{label}]" for label in tracks.values()) +
                                  f"amix=inputs={len(tracks)}:duration=first:normalize=0"])

    def master_graph(self, speech_lufs: float, length: float, *, gain_correction_db: float = 0.0,
                     ending_gain_correction_db: float = 0.0, ending_start: float | None = None) -> str:
        """Output stereo [a] and every stem under the mix's exact limiting gain.

        The limiter applies one gain to every channel. Its quiet constant third
        channel reveals that gain, including the final fade, even on silent or
        cancelling samples. Discard the pilot from the published stereo. Delay
        compensation in master_filter keeps the stems and envelope aligned.
        """
        processing = master_filter(speech_lufs, length, voice_probe=True,
                                   gain_correction_db=gain_correction_db)
        mix = self.mix(stem_probes=True, ending_gain_correction_db=ending_gain_correction_db,
                       ending_start=ending_start)
        envelopes = "".join(f"[master_{track}_envelope]" for track in self.tracks)
        graph = [f"{mix},{processing},asplit=2[master_mix][master_pilot]",
                 "[master_mix]pan=stereo|c0=c0|c1=c1[a]",
                 f"[master_pilot]pan=stereo|c0=c2|c1=c2,volume={1 / LIMITER_PILOT:g},"
                 f"asplit={len(self.tracks)}{envelopes}"]
        for track in self.tracks:
            graph.extend([
                f"[master_{track}_probe]volume={master_gain(speech_lufs) + gain_correction_db:.9f}dB"
                f"[master_{track}_boosted]",
                f"[master_{track}_boosted][master_{track}_envelope]amultiply[{track}_mastered]",
            ])
        return ";".join(graph)


def audio_graph(joined: Path, music: Path | None, voice: list[tuple[Path, float]],
                speech: list[Window], total: float, length: float, *,
                music_volume: float = 1.0, music_gain_db: float = 0.0,
                game_gain_db: float = 0.0, game_duck_db: float = 0.0,
                ending_gain_db: float = 0.0) -> AudioGraph:
    """One source of truth for the mix and all calibration/validation taps."""
    inputs, filters, tracks = ["-i", str(joined)], [], {"game": "g"}
    if music is not None:
        inputs += ["-i", str(music)]
    if voice:
        filters.append(f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                       f"atrim=duration={length},"
                       f"{body_gain_filter(game_gain_db, total, ending_gain_db)},"
                       f"{duck_filter(speech, game_duck_db)}[g]")
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
        calibrated_volume = f"{music_gain_db:.9f}dB" if math.isfinite(music_gain_db) else "0"
        volume = calibrated_volume if voice else str(music_volume)
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
    meter_gain = master_gain(s)
    ending_windows = [(total, length)] if length > total else []
    ending = (raw.measure("game", ending_windows, meter_gain_db=meter_gain)
              if ending_windows else -math.inf)
    ending_gain = s - ENDING_BELOW_VOICE_LU - CEILING_HEADROOM_LU - ending if math.isfinite(ending) else 0.0
    game_pause = raw.measure("game", pauses, meter_gain_db=meter_gain) if pauses else -math.inf
    game_gain = (min(0.0, s - GAME_PAUSE_BELOW_VOICE_LU - CEILING_HEADROOM_LU - game_pause)
                 if math.isfinite(game_pause) else 0.0)
    if game_gain < 0:
        game_gain -= METER_RESOLUTION_LU
    if not ending_windows:
        ending_gain = game_gain
    game = audio_graph(joined, None, voice, speech, total, length,
                       game_gain_db=game_gain, ending_gain_db=ending_gain)
    game_speech = game.measure("game", speech, meter_gain_db=meter_gain)
    game_db = max(0.0, game_speech - (s - GAME_BELOW_VOICE_LU - CEILING_HEADROOM_LU))
    if game_db > 0:
        game_db += METER_RESOLUTION_LU
    game = audio_graph(joined, None, voice, speech, total, length,
                       game_gain_db=game_gain, game_duck_db=game_db, ending_gain_db=ending_gain)
    game_pause = game.measure("game", pauses, meter_gain_db=meter_gain) if pauses else None
    music_pause = (raw.measure("music", pauses if pauses else None, meter_gain_db=meter_gain)
                   if music is not None else None)
    music_db = music_gain(s, music_pause, game_pause) if music_pause is not None else 0.0
    final = audio_graph(joined, music, voice, speech, total, length,
                        music_gain_db=music_db, game_gain_db=game_gain, game_duck_db=game_db,
                        ending_gain_db=ending_gain)
    report = check_balance(
        s, final.measure("music", pauses, meter_gain_db=meter_gain)
        if music is not None and pauses else None,
        final.measure("music", speech, meter_gain_db=meter_gain) if music is not None else None,
        final.measure("game", speech, meter_gain_db=meter_gain), game_pause,
        final.measure("game", ending_windows, meter_gain_db=meter_gain) if ending_windows else None,
    )
    report.update({"speech_lufs": s, "master_gain_db": meter_gain,
                   "music_gain_db": _finite(music_db), "game_gain_db": game_gain, "game_duck_db": game_db,
                   "ending_gain_db": ending_gain, "speech_windows": speech, "pause_windows": pauses,
                   "ending_windows": ending_windows})
    return final, report
