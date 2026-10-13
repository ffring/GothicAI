"""Loudness of the voices. TTS speaks 7-10 dB quieter than the game's own dialogue, so every line is
brought to the level of the original speech, and a limiter keeps the raised peaks from clipping.

The original Russian dialogue is at -16.6 LUFS (median of 435 lines, Xardas -16.3). The engine plays an
original line and a TTS line the same way, so in the game they are as loud as each other (Xardas in one
shot: -18.0 LUFS the original, -17.6..-18.6 the TTS lines). A mood (`style`) keeps the loudness the
provider gives it (Inworld's Xardas: angry about +4 dB after the limiter, hostile -2.5).

Lines are streamed, so the gain has to be known before the first chunk goes out: it comes from the
earlier lines of the same voice. A voice not heard yet gets the usual level of its provider.
"""

from __future__ import annotations

import numpy as np

TARGET_LUFS = -16.0  # the original Russian dialogue of Gothic II
DEFAULT_LUFS = -26.0  # a voice not heard yet: about how loud Inworld and VoxCPM2 speak
MAX_GAIN_DB = 20.0
CEILING_DB = -1.0  # peaks above it are limited
MAX_REDUCTION_DB = 24.0  # by the limiter; the attack ramp takes the whole lookahead for this much
LOOKAHEAD_S = 0.005  # the limiter turns down before a peak, so a line goes out 5 ms later
RELEASE_DB_S = 40.0  # and turns back up after it this fast


def level(pcm: np.ndarray, rate: int) -> float | None:
    """Loudness in LUFS (ITU-R BS.1770): K-weighted mean square of 400 ms blocks 100 ms apart,
    without silence and quiet tails. None for silence."""
    x = _k_weighted(pcm.astype(np.float64) / 32768, rate)
    block, step = int(0.4 * rate), int(0.1 * rate)
    if len(x) < block:
        power = np.array([np.mean(x * x)]) if len(x) else x
    else:
        total = np.concatenate(([0.0], np.cumsum(x * x)))
        starts = np.arange(0, len(x) - block + 1, step)
        power = (total[starts + block] - total[starts]) / block
    power = power[power > 10 ** ((-70 + 0.691) / 10)]  # -70 LUFS: silence
    if not len(power):
        return None
    power = power[power > np.mean(power) / 10]  # 10 LU below the rest: pauses and breaths
    return float(-0.691 + 10 * np.log10(np.mean(power)))


def _k_weighted(x: np.ndarray, rate: int) -> np.ndarray:
    """The K filter of BS.1770 (a high shelf for the head, a high-pass), applied by its magnitude
    in the frequency domain: the phase does not change the power of a 400 ms block."""
    if not len(x):
        return x
    n = 1 << (len(x) + rate // 10).bit_length()  # zero padding against wrap-around
    z = np.exp(-1j * np.pi * np.arange(n // 2 + 1) / (n // 2))  # z^-1 at each bin

    def biquad(b: tuple[float, float, float], a: tuple[float, float, float]) -> np.ndarray:
        return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)

    # cookbook biquads with the parameters pyloudnorm fits to the standard's 48 kHz coefficients
    w = 2 * np.pi * 1681.974450955533 / rate
    a = 10 ** (3.999843853973347 / 40)
    cos, s = np.cos(w), np.sqrt(a) * np.sin(w) / 0.7071752369554196
    shelf = biquad(
        (a * (a + 1 + (a - 1) * cos + s), -2 * a * (a - 1 + (a + 1) * cos), a * (a + 1 + (a - 1) * cos - s)),
        (a + 1 - (a - 1) * cos + s, 2 * (a - 1 - (a + 1) * cos), a + 1 - (a - 1) * cos - s),
    )
    w = 2 * np.pi * 38.13547087602444 / rate
    cos, alpha = np.cos(w), np.sin(w) / (2 * 0.5003270373238773)
    high_pass = biquad(((1 + cos) / 2, -(1 + cos), (1 + cos) / 2), (1 + alpha, -2 * cos, 1 - alpha))
    return np.fft.irfft(np.fft.rfft(x, n) * np.abs(shelf * high_pass), n)[: len(x)]


class Line:
    """One line: a fixed gain and a lookahead peak limiter over its chunks as they come."""

    def __init__(self, rate: int, gain_db: float) -> None:
        self.gain_db = gain_db
        self._gain = 10 ** (gain_db / 20)
        self._rate = rate
        self._lookahead = max(1, int(rate * LOOKAHEAD_S))
        self._attack = MAX_REDUCTION_DB / self._lookahead  # dB per sample
        self._release = RELEASE_DB_S / rate
        self._reduction = 0.0  # dB, on the last sample sent
        self._held = np.zeros(0)  # raised samples not sent yet: the limiter's lookahead
        self._source: list[np.ndarray] = []

    def process(self, pcm: bytes) -> bytes:
        """Takes the next chunk of 16-bit PCM; returns what can go out now (all but the lookahead)."""
        src = np.frombuffer(pcm, "<i2")
        self._source.append(src)
        x = np.concatenate((self._held, src * self._gain))
        return self._send(x, len(x) - self._lookahead)

    def flush(self) -> bytes:
        """The end of the line: what the lookahead still holds."""
        return self._send(self._held, len(self._held))

    def level(self) -> float | None:
        """Of the provider's audio, before the gain."""
        return level(np.concatenate(self._source), self._rate) if self._source else None

    def _send(self, x: np.ndarray, n: int) -> bytes:
        if n <= 0:
            self._held = x
            return b""
        # the reduction each sample needs to stay under the ceiling
        need = CEILING_DB - 20 * np.log10(np.maximum(np.abs(x), 1.0) / 32768)
        need = np.clip(need, -MAX_REDUCTION_DB, 0.0)
        # attack: reach it gradually before the peak, at most `_attack` dB per sample: the minimum
        # over later samples of need[k] + attack*(k-i). Samples past the lookahead add nothing:
        # for them that sum is above 0, as attack*lookahead is the deepest reduction
        i = np.arange(len(x))
        down = np.minimum.accumulate((need + self._attack * i)[::-1])[::-1] - self._attack * i
        # release: rise back at most `_release` dB per sample, starting from the previous chunk;
        # the recursion g[i] = min(down[i], g[i-1] + release) in closed form
        i, down = i[:n], down[:n]
        gain = self._release * i + np.minimum(
            self._reduction + self._release, np.minimum.accumulate(down - self._release * i)
        )
        self._reduction = float(gain[-1])
        out = x[:n] * 10 ** (gain / 20)
        self._held = x[n:]
        return np.clip(np.round(out), -32768, 32767).astype("<i2").tobytes()


class Leveler:
    """Remembers how loud each voice speaks and evens its lines out to `target_lufs`."""

    def __init__(self, target_lufs: float = TARGET_LUFS) -> None:
        self.target_lufs = target_lufs
        self._levels: dict[tuple[str, str], float] = {}  # (provider, voice id) -> LUFS

    def start(self, provider: str, voice_id: str, rate: int) -> Line:
        known = self._levels.get((provider, voice_id))
        if known is None:
            same = [v for (p, _), v in self._levels.items() if p == provider]
            known = sum(same) / len(same) if same else DEFAULT_LUFS
        return Line(rate, min(self.target_lufs - known, MAX_GAIN_DB))

    def learn(self, provider: str, voice_id: str, line: Line) -> None:
        """After a line is spoken: its level moves the voice's halfway, lines vary by a dB or two."""
        heard = line.level()
        if heard is None:
            return
        key = (provider, voice_id)
        old = self._levels.get(key)
        self._levels[key] = heard if old is None else (old + heard) / 2
