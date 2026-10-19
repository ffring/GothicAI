"""Colour limits for generated material albedo; never change spatial detail."""

import numpy as np


_LUMINANCE = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)


def _linear(srgb):
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def _srgb(linear):
    return np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)


def _mean_saturation(rgb):
    maximum = np.max(rgb, axis=-1)
    minimum = np.min(rgb, axis=-1)
    saturation = np.divide(maximum - minimum, maximum,
                           out=np.zeros_like(maximum, dtype=np.float64), where=maximum > 0)
    return float(saturation.mean())


def limit_saturation(rgb_float_0_1, limit):
    """Cap mean per-pixel sRGB HSV S in the returned 8-bit RGB image.

    Every pixel uses the same blend factor towards a neutral grey with that
    pixel's Rec.709 *linear* luminance. This preserves each pixel's luminance
    before 8-bit quantization. The cap is evaluated after quantization, including
    when rounding alone would take an otherwise compliant image over the limit.
    ``gray_blend_factor`` in the metadata is zero for no blend and one for grey.
    """
    rgb = np.asarray(rgb_float_0_1, dtype=np.float64)
    if rgb.ndim < 1 or rgb.shape[-1] != 3 or rgb.size == 0:
        raise ValueError('expected nonempty RGB pixels with a final dimension of 3')
    if not np.isfinite(rgb).all() or np.any(rgb < 0) or np.any(rgb > 1):
        raise ValueError('RGB values must be finite and in [0, 1]')
    limit = float(limit)
    if not np.isfinite(limit) or not 0 <= limit <= 1:
        raise ValueError('saturation limit must be finite and in [0, 1]')

    linear = _linear(rgb)
    luminance = linear @ _LUMINANCE
    original_quantized = np.rint(rgb * 255).astype(np.uint8)
    quantized_before = _mean_saturation(original_quantized.astype(np.float64))
    factor = 0.0
    quantized = original_quantized

    if quantized_before > limit:
        # A shared grey blend preserves channel order. Only the two extrema are
        # needed to measure HSV S, avoiding three-channel arrays at each step.
        minimum = linear.min(axis=-1)
        maximum = linear.max(axis=-1)

        def quantized_saturation(blend):
            low = np.rint(np.clip(_srgb((1 - blend) * minimum + blend * luminance), 0, 1) * 255)
            high = np.rint(np.clip(_srgb((1 - blend) * maximum + blend * luminance), 0, 1) * 255)
            return float(np.divide(high - low, high, out=np.zeros_like(high), where=high > 0).mean())

        left, right = 0.0, 1.0
        for _ in range(28):
            candidate = (left + right) / 2
            if quantized_saturation(candidate) <= limit:
                right = candidate
            else:
                left = candidate
        factor = right
        adjusted = (1 - factor) * linear + factor * luminance[..., None]
        quantized = np.rint(np.clip(_srgb(adjusted), 0, 1) * 255).astype(np.uint8)

    after = _mean_saturation(quantized.astype(np.float64))
    metadata = dict(
        method='uniform linear RGB blend towards per-pixel Rec.709 luminance; no spatial edits',
        threshold=limit,
        mean_hsv_s_before=_mean_saturation(rgb),
        quantized_mean_hsv_s_before=quantized_before,
        mean_hsv_s_after=after,
        gray_blend_factor=factor,
        linear_mean_before=float(luminance.mean()),
        linear_mean_after=float((_linear(quantized.astype(np.float64) / 255) @ _LUMINANCE).mean()),
    )
    return quantized, metadata
