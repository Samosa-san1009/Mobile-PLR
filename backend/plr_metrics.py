"""Derive initial PLR summary metrics from per-frame diameter predictions.

These formulas are intentionally simple first-pass definitions:
  * baseline: median smoothed diameter before flash onset
  * minimum: minimum smoothed diameter after flash onset
  * amplitude: baseline - minimum
  * latency: elapsed time from flash onset to the minimum

Values remain in pixels until a validated pixel-to-mm calibration exists.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np


def _moving_median(values: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window == 1 or len(values) < 2:
        return values.copy()

    radius = window // 2
    smoothed = np.empty_like(values, dtype=float)
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        smoothed[index] = float(np.median(values[start:end]))
    return smoothed


def calculate_plr_metrics(
    frame_indices: Iterable[int],
    diameters_px: Iterable[float],
    fps: float,
    flash_onset_s: float,
    smoothing_window: int = 5,
) -> dict:
    """Return aggregate metrics and a serializable per-frame time series."""
    frames = np.asarray(list(frame_indices), dtype=int)
    values = np.asarray(list(diameters_px), dtype=float)

    if len(frames) == 0 or len(values) == 0 or len(frames) != len(values):
        raise ValueError("Predictions must contain matching non-empty frame and diameter arrays")
    if fps <= 0:
        raise ValueError("fps must be positive")

    valid = np.isfinite(values)
    frames = frames[valid]
    values = values[valid]
    if len(values) == 0:
        raise ValueError("Predictions contain no finite diameter values")

    times_s = frames.astype(float) / float(fps)
    smoothed = _moving_median(values, smoothing_window)

    pre_mask = times_s < flash_onset_s
    post_mask = times_s >= flash_onset_s

    # If a clip has no usable pre-flash frames, use its first few predictions
    # as a clearly marked fallback instead of failing the entire session.
    baseline_source = smoothed[pre_mask]
    baseline_fallback = False
    if len(baseline_source) == 0:
        baseline_source = smoothed[: min(5, len(smoothed))]
        baseline_fallback = True

    post_indices = np.flatnonzero(post_mask)
    if len(post_indices) == 0:
        raise ValueError("Clip contains no predictions at or after flash onset")

    baseline = float(np.median(baseline_source))
    minimum_relative_index = int(np.argmin(smoothed[post_indices]))
    minimum_index = int(post_indices[minimum_relative_index])
    minimum = float(smoothed[minimum_index])
    latency_ms = max(0.0, (times_s[minimum_index] - flash_onset_s) * 1000.0)

    series = [
        {
            "frame": int(frame),
            "time_ms": round(float(time_s * 1000.0), 3),
            "diameter_px": round(float(raw), 6),
            "smoothed_diameter_px": round(float(smooth), 6),
        }
        for frame, time_s, raw, smooth in zip(frames, times_s, values, smoothed)
    ]

    return {
        "unit": "px",
        "baseline_diameter_px": round(baseline, 6),
        "min_diameter_px": round(minimum, 6),
        "constriction_amplitude_px": round(baseline - minimum, 6),
        "latency_ms": round(latency_ms, 3),
        "flash_onset_ms": round(float(flash_onset_s * 1000.0), 3),
        "fps": round(float(fps), 6),
        "baseline_fallback_used": baseline_fallback,
        "formula_version": "initial-v1",
        "diameter_series": series,
    }
