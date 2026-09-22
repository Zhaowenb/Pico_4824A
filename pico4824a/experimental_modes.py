"""Experimental two-channel spatial-temporal symmetry and reflection matching."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _shift(values: np.ndarray, samples: int) -> np.ndarray:
    shifted = np.roll(values, samples).copy()
    if samples > 0:
        shifted[:samples] = 0.0
    elif samples < 0:
        shifted[samples:] = 0.0
    return shifted


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(values, dtype=np.float64) ** 2)))


def _symmetry_metrics(
    common: np.ndarray, differential: np.ndarray, mask: np.ndarray
) -> dict[str, float]:
    common_rms = _rms(common[mask])
    differential_rms = _rms(differential[mask])
    return {
        "common_rms_v": common_rms,
        "differential_rms_v": differential_rms,
        "differential_to_common_db": 20.0 * math.log10(
            max(differential_rms, 1e-30) / max(common_rms, 1e-30)
        ),
    }


def _best_alignment(
    reference: np.ndarray,
    moving: np.ndarray,
    mask: np.ndarray,
    maximum_shift_samples: int,
) -> tuple[np.ndarray, float, int, float]:
    indices = np.flatnonzero(mask)
    if indices.size < 16:
        raise ValueError("双通道校准时间窗至少需要16个采样点")
    best_error = math.inf
    best_shifted = moving
    best_gain = 1.0
    best_shift = 0
    best_correlation = 0.0
    reference_fit = reference[indices]
    for shift in range(-maximum_shift_samples, maximum_shift_samples + 1):
        shifted = _shift(moving, shift)
        fit = shifted[indices]
        gain = float(np.dot(fit, reference_fit) / max(float(np.dot(fit, fit)), 1e-30))
        residual = reference_fit - gain * fit
        error = float(np.mean(residual**2))
        if error < best_error:
            denominator = math.sqrt(
                max(float(np.dot(reference_fit, reference_fit) * np.dot(fit, fit)), 1e-30)
            )
            best_error = error
            best_shifted = shifted
            best_gain = gain
            best_shift = shift
            best_correlation = float(np.dot(reference_fit, fit) / denominator)
    return best_shifted, best_gain, best_shift, best_correlation


def _fft_correlate_valid(signal: np.ndarray, template: np.ndarray) -> np.ndarray:
    size = signal.size + template.size - 1
    fft_size = 1 << int(math.ceil(math.log2(max(size, 2))))
    convolution = np.fft.irfft(
        np.fft.rfft(signal, fft_size) * np.fft.rfft(template[::-1], fft_size),
        fft_size,
    )[:size]
    return convolution[template.size - 1 : signal.size]


def _normalized_match(signal: np.ndarray, template: np.ndarray) -> np.ndarray:
    template = np.asarray(template, dtype=np.float64) - float(np.mean(template))
    signal = np.asarray(signal, dtype=np.float64)
    length = template.size
    if signal.size < length or length < 16:
        raise ValueError("端面模板必须至少16点，且搜索区间必须比模板更长")
    numerator = _fft_correlate_valid(signal, template)
    cumulative = np.concatenate(([0.0], np.cumsum(signal)))
    cumulative_squared = np.concatenate(([0.0], np.cumsum(signal**2)))
    sums = cumulative[length:] - cumulative[:-length]
    energies = cumulative_squared[length:] - cumulative_squared[:-length] - sums**2 / length
    template_energy = max(float(np.dot(template, template)), 1e-30)
    return numerator / np.sqrt(np.maximum(energies * template_energy, 1e-30))


def _candidate_peaks(
    times_us: np.ndarray,
    correlation: np.ndarray,
    count: int,
    minimum_separation_samples: int,
) -> list[dict[str, float]]:
    order = np.argsort(np.abs(correlation))[::-1]
    chosen: list[int] = []
    for index in order:
        if all(abs(int(index) - other) >= minimum_separation_samples for other in chosen):
            chosen.append(int(index))
        if len(chosen) >= count:
            break
    chosen.sort()
    return [
        {
            "time_us": float(times_us[index]),
            "correlation": float(correlation[index]),
            "absolute_correlation": float(abs(correlation[index])),
            "polarity": 1.0 if correlation[index] >= 0 else -1.0,
        }
        for index in chosen
    ]


def experimental_mode_analysis(
    time_s: np.ndarray,
    reference_channel: np.ndarray,
    comparison_channel: np.ndarray,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Return evidence only; this function intentionally makes no defect decision."""
    time_s = np.asarray(time_s, dtype=np.float64)
    time_us = time_s * 1e6
    reference_name = str(raw.get("reference_channel", "A")).upper()
    comparison_name = str(raw.get("comparison_channel", "G")).upper()
    reference_channel = np.asarray(reference_channel, dtype=np.float64)
    comparison_channel = np.asarray(comparison_channel, dtype=np.float64)
    sample_rate_hz = 1.0 / float(np.median(np.diff(time_s)))
    direct_start_us = float(raw.get("direct_start_us", 150.0))
    direct_end_us = float(raw.get("direct_end_us", 270.0))
    end_start_us = float(raw.get("end_start_us", 630.0))
    end_end_us = float(raw.get("end_end_us", 760.0))
    search_start_us = float(raw.get("search_start_us", direct_end_us))
    search_end_us = float(raw.get("search_end_us", end_start_us))
    maximum_shift_us = float(raw.get("maximum_shift_us", 2.0))
    candidate_count = int(raw.get("candidate_count", 6))
    minimum_separation_us = float(raw.get("minimum_separation_us", 30.0))
    temporal_window_us = float(raw.get("temporal_window_us", 20.0))
    if temporal_window_us <= 0:
        raise ValueError("时间对称性滑动窗必须大于0")
    if not direct_start_us < direct_end_us <= search_start_us < search_end_us <= end_start_us < end_end_us:
        raise ValueError(
            "实验窗口必须按“直达校准窗→候选搜索窗→端面模板窗”的时间顺序设置"
        )
    direct_mask = (time_us >= direct_start_us) & (time_us <= direct_end_us)
    maximum_shift_samples = max(0, round(maximum_shift_us * 1e-6 * sample_rate_hz))
    aligned_comparison, gain, shift_samples, direct_correlation = _best_alignment(
        reference_channel, comparison_channel, direct_mask, maximum_shift_samples
    )
    calibrated_comparison = gain * aligned_comparison
    common = 0.5 * (reference_channel + calibrated_comparison)
    differential = 0.5 * (reference_channel - calibrated_comparison)

    search_mask = (time_us >= search_start_us) & (time_us <= search_end_us)
    template_mask = (time_us >= end_start_us) & (time_us <= end_end_us)
    search_indices = np.flatnonzero(search_mask)
    template = common[template_mask]
    correlation = _normalized_match(common[search_mask], template)
    center_indices = search_indices[: correlation.size] + template.size // 2
    center_indices = np.minimum(center_indices, time_s.size - 1)
    match_times_us = time_us[center_indices]
    candidates = _candidate_peaks(
        match_times_us,
        correlation,
        max(1, min(candidate_count, 20)),
        max(1, round(minimum_separation_us * 1e-6 * sample_rate_hz)),
    )
    analysis_mask = (time_us >= search_start_us) & (time_us <= end_end_us)
    direct_metrics = _symmetry_metrics(common, differential, direct_mask)
    search_metrics = _symmetry_metrics(common, differential, search_mask)
    end_metrics = _symmetry_metrics(common, differential, template_mask)
    overall_metrics = _symmetry_metrics(common, differential, analysis_mask)
    temporal_samples = max(3, round(temporal_window_us * 1e-6 * sample_rate_hz))
    temporal_samples = min(temporal_samples, time_s.size)
    kernel = np.ones(temporal_samples, dtype=np.float64) / temporal_samples
    common_energy = np.convolve(common**2, kernel, mode="same")
    differential_energy = np.convolve(differential**2, kernel, mode="same")
    temporal_symmetry_db = 10.0 * np.log10(
        np.maximum(differential_energy, 1e-30)
        / np.maximum(common_energy, 1e-30)
    )
    display_step = max(1, int(math.ceil(time_s.size / 5000)))
    match_step = max(1, int(math.ceil(correlation.size / 4000)))
    return {
        "status": "experimental_test_only",
        "reference_channel": reference_name,
        "comparison_channel": comparison_name,
        "warning": (
            f"{reference_name}/{comparison_name}安装角度、灵敏度和目标模态尚未固定；"
            "结果仅作为空间—时间对称性和"
            "端面相似度证据，不得直接判定缺陷。"
        ),
        "time_us": time_us[::display_step].tolist(),
        "common_v": common[::display_step].tolist(),
        "differential_v": differential[::display_step].tolist(),
        "match_time_us": match_times_us[::match_step].tolist(),
        "match_correlation": correlation[::match_step].tolist(),
        "symmetry_time_us": time_us[::display_step].tolist(),
        "temporal_symmetry_db": temporal_symmetry_db[::display_step].tolist(),
        "candidates": candidates,
        "calibration": {
            "comparison_to_reference_gain": gain,
            "comparison_shift_samples": shift_samples,
            "comparison_shift_us": shift_samples / sample_rate_hz * 1e6,
            "direct_window_correlation": direct_correlation,
        },
        "metrics": overall_metrics,
        "window_metrics": {
            "direct": direct_metrics,
            "search": search_metrics,
            "end_reflection": end_metrics,
        },
        "windows": {
            "direct_start_us": direct_start_us,
            "direct_end_us": direct_end_us,
            "search_start_us": search_start_us,
            "search_end_us": search_end_us,
            "end_start_us": end_start_us,
            "end_end_us": end_end_us,
            "temporal_window_us": temporal_window_us,
        },
    }
