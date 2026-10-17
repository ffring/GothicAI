"""Detect isolated sharp corners in a finished short's decoded audio."""

from __future__ import annotations

import subprocess
import sys
from array import array
from pathlib import Path

SAMPLE_RATE = 48_000
LOCAL_SAMPLES = round(0.010 * SAMPLE_RATE)
MERGE_SAMPLES = round(0.005 * SAMPLE_RATE)
RATIO_THRESHOLD = 25.0
MIN_CURVATURE = 0.01


def detect(path: Path) -> list[float]:
    """Return click times in seconds, grouping hits less than 5 ms apart.

    The second difference locates sharp corners relative to their surrounding
    10 ms of audio. A group's timestamp is its strongest corner. Decode errors
    propagate so a failed inspection cannot be mistaken for a clean soundtrack.
    """
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        check=True, capture_output=True,
    )
    samples = array("f")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    if len(samples) < 3:
        return []

    curvature = array("f", (
        abs(samples[index + 2] - 2 * samples[index + 1] + samples[index])
        for index in range(len(samples) - 2)
    ))
    # A centered, fixed-width moving average, zero-padded at the file edges.
    half = LOCAL_SAMPLES // 2
    local_sum = sum(curvature[:half])
    hits: list[float] = []
    previous_hit = -MERGE_SAMPLES
    peak_index, peak_value = -1, 0.0
    for index, value in enumerate(curvature):
        right = index + half
        if right < len(curvature):
            local_sum += curvature[right]
        left = index - half
        if left >= 0:
            local_sum -= curvature[left]
        local = local_sum / LOCAL_SAMPLES + 1e-5
        if value <= MIN_CURVATURE or value / local <= RATIO_THRESHOLD:
            continue
        if index - previous_hit >= MERGE_SAMPLES:
            if peak_index >= 0:
                hits.append((peak_index + 1) / SAMPLE_RATE)
            peak_index, peak_value = index, value
        elif value > peak_value:
            peak_index, peak_value = index, value
        previous_hit = index
    if peak_index >= 0:
        hits.append((peak_index + 1) / SAMPLE_RATE)
    return hits
