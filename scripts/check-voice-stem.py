#!/usr/bin/env python3
"""Check an offline tts recording pair; print measurements for the PR description.

Usage: scripts/check-voice-stem.py <control-directory> <stem-directory>
The directories come from GOTHIC_TTS_RECORD=1 SCENARIO=tts scripts/e2e.sh,
with OPENGOTHIC_BENCH_VOICE_STEM unset and set to 1, respectively.
Detect the fake provider's tone with Goertzel: it must fall by at least 20 dB
or reach the adjacent-band noise floor (+3 dB). World ambience remains audible.
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

# This provider and its package imports use only the standard library; importing starts no services.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tts-service" / "src"))
from gothic_tts.providers.fake import FakeProvider


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


def band_power(samples: array, channels: int, rate: int, start: int, stop: int, frequency: float) -> float:
    """Goertzel tone RMS squared; average channel powers without stereo phase cancellation."""
    coefficient = 2 * math.cos(math.tau * frequency / rate)
    power = 0.0
    for channel in range(channels):
        previous, older = 0.0, 0.0
        for index in range(start * channels + channel, stop * channels, channels):
            current = samples[index] + coefficient * previous - older
            older, previous = previous, current
        power += max(0.0, previous * previous + older * older - coefficient * previous * older)
    return 2 * power / (channels * (stop - start) ** 2)


def dbfs(power: float) -> float:
    return 10 * math.log10(power / 32768**2) if power > 0 else -math.inf


def band_levels(samples: array, channels: int, rate: int, line: dict, end: dict, fps: int) -> tuple[float, float]:
    # Each take uses its own speech window, excluding 50 ms at each edge.
    start = round((line["frame"] / fps + 0.05) * rate)
    stop = round((end["frame"] / fps - 0.05) * rate)
    require(0 <= start < stop <= len(samples) // channels, "invalid tone comparison window")
    frequency = FakeProvider.tone_frequency_hz
    tone = band_power(samples, channels, rate, start, stop, frequency)
    neighbors = [band_power(samples, channels, rate, start, stop, frequency * factor)
                 for factor in (0.73, 1.37)]
    return dbfs(tone), dbfs(sum(neighbors) / len(neighbors))


def check(control: Path, stem: Path) -> None:
    control_events, stem_events = events(control), events(stem)
    control_lines = [e for e in control_events if e["kind"] == "npc_line" and e["seg"] == "tts"]
    stem_lines = [e for e in stem_events if e["kind"] == "npc_line" and e["seg"] == "tts"]
    voices = [e for e in stem_events if e["kind"] == "npc_voice"]
    ends = [e for e in stem_events if e["kind"] == "npc_voice_end" and e["seg"] == "tts"]
    control_ends = [e for e in control_events if e["kind"] == "npc_voice_end" and e["seg"] == "tts"]
    require(voices, "no npc_voice event in the flagged recording")
    require(not any(e["kind"] == "npc_voice" for e in control_events), "control emitted npc_voice")
    require(not list(control.glob("*.voice-*.wav")), "control wrote unexpected voice stems")
    require(len(voices) == len(stem_lines) == len(control_lines) == len(ends) == len(control_ends),
            f"voice/line/end counts differ: {len(voices)}/{len(stem_lines)}/{len(control_lines)}/{len(ends)}/{len(control_ends)}")
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
    control_rate, control_channels, _, control_mix = read_wav(control / "tts.wav")
    require(rate == control_rate == 48000, f"expected 48 kHz mixes, got {rate}/{control_rate}")
    require(len(list(stem.glob("*.voice-*.wav"))) == len(voices), "unreferenced or missing stem files")
    print(f"npc_voice events={len(voices)}, video={fps} fps, mix={rate} Hz, tone={FakeProvider.tone_frequency_hz:.6f} Hz")
    for index, (voice, line, end, control_line, control_end) in enumerate(zip(voices, stem_lines, ends, control_lines, control_ends), 1):
        require(voice["seg"] == "tts" and voice["npc"] == line["npc"] == end["npc"],
                f"voice {index}: wrong segment/NPC")
        require(bool(voice["line"]), f"voice {index}: missing line ID")
        recorded_path = Path(voice["wav"])
        require(recorded_path.is_absolute(), f"voice {index}: WAV path must be absolute")
        require(recorded_path.name == f"tts.voice-{index:02d}.wav", f"voice {index}: unexpected WAV name")
        voice_rate, voice_channels, voice_frames, voice_pcm = read_wav(stem / recorded_path.name)
        require(voice_rate == FakeProvider.rate and voice_channels == 1,
                f"voice {index}: fake TTS stem should be mono {FakeProvider.rate} Hz, got {voice_channels}/{voice_rate}")
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
        require(control_end["npc"] == control_line["npc"], f"voice {index}: wrong control end NPC")
        frames = round(duration * rate)
        require(sample + frames <= total, f"voice {index}: voice extends past the mix")
        control_tone, control_noise = band_levels(control_mix, control_channels, control_rate, control_line, control_end, fps)
        stem_tone, stem_noise = band_levels(mix, channels, rate, line, end, fps)
        require(math.isfinite(control_tone), f"voice {index}: control has no audible tone")
        reduction = control_tone - stem_tone
        at_noise_floor = stem_tone <= stem_noise + 3
        criterion = "20 dB reduction" if reduction >= 20 else "noise floor +3 dB" if at_noise_floor else "FAILED"
        print(f"{recorded_path.name}: line={voice['line']}, sample={sample}, "
              f"PCM16 mono {voice_rate} Hz, {voice_frames} samples, {duration:.6f}s; "
              f"raw voice={raw_duration:.6f}s, playback={playback:.6f}s, error={error * 1000:.3f}ms; "
              f"control tone/noise={control_tone:.2f}/{control_noise:.2f} dBFS, "
              f"stem tone/noise={stem_tone:.2f}/{stem_noise:.2f} dBFS, "
              f"reduction={reduction:.2f} dB, criterion={criterion}")
        require(reduction >= 20 or at_noise_floor,
                f"voice {index}: tone reduction {reduction:.2f} dB < 20 dB and "
                f"stem tone {stem_tone:.2f} dBFS > adjacent bands {stem_noise:.2f} dBFS +3 dB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control", type=Path)
    parser.add_argument("stem", type=Path)
    args = parser.parse_args()
    check(args.control, args.stem)


if __name__ == "__main__":
    main()
