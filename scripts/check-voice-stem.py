#!/usr/bin/env python3
"""Check an offline tts recording pair; print measurements for the PR description.

Usage: scripts/check-voice-stem.py <control-directory> <stem-directory>
The directories come from GOTHIC_TTS_RECORD=1 SCENARIO=tts scripts/e2e.sh,
with OPENGOTHIC_BENCH_VOICE_STEM unset and set to 1, respectively.
"""

from __future__ import annotations

import argparse
from array import array
import json
import math
from pathlib import Path
import re
import sys
import wave


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def events(directory: Path) -> list[dict]:
    return [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]


def read_wav(path: Path) -> tuple[int, int, int, array]:
    with wave.open(str(path), "rb") as wav:
        require(wav.getsampwidth() == 2, f"{path}: expected 16-bit PCM")
        require(wav.getcomptype() == "NONE", f"{path}: expected uncompressed PCM")
        rate, channels, frames = wav.getframerate(), wav.getnchannels(), wav.getnframes()
        samples = array("h", wav.readframes(frames))
    if sys.byteorder != "little":
        samples.byteswap()
    return rate, channels, frames, samples


def rms(samples: array, channels: int, start: int, frames: int) -> float:
    window = samples[start * channels : (start + frames) * channels]
    require(bool(window), "empty comparison window")
    return math.sqrt(sum(float(value) ** 2 for value in window) / len(window))


def dbfs(value: float) -> float:
    return 20 * math.log10(value / 32768) if value > 0 else -math.inf


def check(control: Path, stem: Path) -> None:
    control_events, stem_events = events(control), events(stem)
    control_lines = [e for e in control_events if e["kind"] == "npc_line" and e["seg"] == "tts"]
    stem_lines = [e for e in stem_events if e["kind"] == "npc_line" and e["seg"] == "tts"]
    voices = [e for e in stem_events if e["kind"] == "npc_voice"]
    ends = [e for e in stem_events if e["kind"] == "npc_voice_end" and e["seg"] == "tts"]
    require(voices, "no npc_voice event in the flagged recording")
    require(not any(e["kind"] == "npc_voice" for e in control_events), "control emitted npc_voice")
    require(not list(control.glob("*.voice-*.wav")), "control wrote unexpected voice stems")
    require(len(voices) == len(stem_lines) == len(control_lines) == len(ends),
            f"voice/line/end counts differ: {len(voices)}/{len(stem_lines)}/{len(control_lines)}/{len(ends)}")
    started_ids = re.findall(r"\[ai\] voice (\S+) starts with", (stem / "game.log").read_text())
    require([voice["line"] for voice in voices] == started_ids,
            "npc_voice line IDs differ from the engine's started voices")
    require([(e["npc"], e["text"]) for e in control_lines] ==
            [(e["npc"], e["text"]) for e in stem_lines], "the two takes contain different lines")
    rec = next(e for e in stem_events if e["kind"] == "rec" and e["seg"] == "tts")
    control_rec = next(e for e in control_events if e["kind"] == "rec" and e["seg"] == "tts")
    require(rec["fps"] == control_rec["fps"], "video frame rates differ")
    fps = rec["fps"]
    rate, channels, total, mix = read_wav(stem / "tts.wav")
    control_rate, control_channels, control_total, control_mix = read_wav(control / "tts.wav")
    require(rate == control_rate == 48000, f"expected 48 kHz mixes, got {rate}/{control_rate}")
    require(len(list(stem.glob("*.voice-*.wav"))) == len(voices), "unreferenced or missing stem files")
    print(f"npc_voice events={len(voices)}, video={fps} fps, mix={rate} Hz")
    for index, (voice, line, end, control_line) in enumerate(zip(voices, stem_lines, ends, control_lines), 1):
        require(voice["seg"] == "tts" and voice["npc"] == line["npc"] == end["npc"],
                f"voice {index}: wrong segment/NPC")
        require(bool(voice["line"]), f"voice {index}: missing line ID")
        recorded_path = Path(voice["wav"])
        require(recorded_path.is_absolute(), f"voice {index}: WAV path must be absolute")
        require(recorded_path.name == f"tts.voice-{index:02d}.wav", f"voice {index}: unexpected WAV name")
        voice_rate, voice_channels, voice_frames, voice_pcm = read_wav(stem / recorded_path.name)
        require(voice_rate == 16000 and voice_channels == 1,
                f"voice {index}: fake TTS stem should be mono 16000 Hz, got {voice_channels}/{voice_rate}")
        require(any(voice_pcm), f"voice {index}: stem is silent")
        duration = voice_frames / voice_rate
        raw_duration = end["ms"] / 1000
        playback = (end["frame"] - voice["frame"]) / fps
        error = abs(duration - playback)
        require(error <= 1 / fps,
                f"voice {index}: duration {duration:.6f}s vs playback {playback:.6f}s, error > 1 frame")
        require(duration >= raw_duration - 1 / voice_rate, f"voice {index}: raw voice samples were lost")
        sample = voice["sample"]
        require(isinstance(sample, int) and 0 <= sample < total, f"voice {index}: invalid sample offset")
        require(voice["frame"] >= 0, f"voice {index}: invalid video frame")
        require(voice["frame"] == line["frame"], f"voice {index}: wrong video frame")
        require(abs(sample / rate - voice["frame"] / fps) <= 1 / fps,
                f"voice {index}: sample offset differs from video time by more than a frame")
        frames = round(duration * rate)
        control_sample = round(control_line["frame"] * rate / fps)
        require(sample + frames <= total and control_sample + frames <= control_total,
                f"voice {index}: voice extends past the mix")
        stem_rms = rms(mix, channels, sample, frames)
        control_rms = rms(control_mix, control_channels, control_sample, frames)
        require(control_rms > 0, f"voice {index}: control has no audible voice")
        reduction = 20 * math.log10(control_rms / stem_rms) if stem_rms else math.inf
        print(f"{recorded_path.name}: line={voice['line']}, sample={sample}, "
              f"PCM16 mono {voice_rate} Hz, {voice_frames} samples, {duration:.6f}s; "
              f"raw voice={raw_duration:.6f}s, playback={playback:.6f}s, error={error * 1000:.3f}ms; "
              f"mix RMS control={dbfs(control_rms):.2f} dBFS, stem={dbfs(stem_rms):.2f} dBFS, "
              f"reduction={reduction:.2f} dB")
        require(reduction >= 20, f"voice {index}: mix reduction {reduction:.2f} dB < 20 dB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control", type=Path)
    parser.add_argument("stem", type=Path)
    args = parser.parse_args()
    check(args.control, args.stem)


if __name__ == "__main__":
    main()
