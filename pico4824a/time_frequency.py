"""STFT, wavelet packet, and Morlet CWT maps without optional dependencies."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _segment(
    time_s: np.ndarray,
    values: np.ndarray,
    start_us: float,
    end_us: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    mask = (time_s * 1e6 >= start_us) & (time_s * 1e6 <= end_us)
    indices = np.flatnonzero(mask)
    if indices.size < 32:
        raise ValueError("时频分析区间至少需要32个采样点")
    local_time = np.asarray(time_s[indices], dtype=np.float64)
    local_values = np.asarray(values[indices], dtype=np.float64)
    sample_rate_hz = 1.0 / float(np.median(np.diff(local_time)))
    return local_time, local_values - float(np.mean(local_values)), sample_rate_hz


def _to_relative_db(power: np.ndarray, floor_db: float) -> np.ndarray:
    maximum = max(float(np.max(power)), 1e-30)
    db = 10.0 * np.log10(np.maximum(power, maximum * 1e-15) / maximum)
    return np.maximum(db, floor_db)


def _limit_axis(
    coordinates: np.ndarray,
    matrix: np.ndarray,
    maximum: int,
    axis: int,
) -> tuple[np.ndarray, np.ndarray]:
    if coordinates.size <= maximum:
        return coordinates, matrix
    indices = np.linspace(0, coordinates.size - 1, maximum).round().astype(np.int64)
    return coordinates[indices], np.take(matrix, indices, axis=axis)


def stft_map(
    time_s: np.ndarray,
    values: np.ndarray,
    sample_rate_hz: float,
    raw: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    window_us = float(raw.get("window_us", 80.0))
    overlap_ratio = float(raw.get("overlap_ratio", 0.85))
    if window_us <= 0:
        raise ValueError("STFT窗长度必须大于0")
    if not 0.0 <= overlap_ratio < 0.98:
        raise ValueError("STFT重叠率必须在0%到98%之间")
    samples = max(16, round(window_us * 1e-6 * sample_rate_hz))
    samples = min(samples, values.size)
    hop = max(1, round(samples * (1.0 - overlap_ratio)))
    starts = np.arange(0, values.size - samples + 1, hop, dtype=np.int64)
    if starts.size == 0:
        starts = np.array([0], dtype=np.int64)
    window_name = str(raw.get("window_function", "hann"))
    if window_name == "hann":
        window = np.hanning(samples)
    elif window_name == "blackman":
        window = np.blackman(samples)
    else:
        window = np.ones(samples)
    requested_fft = int(raw.get("fft_samples", 0))
    fft_samples = (
        max(samples, requested_fft)
        if requested_fft > 0
        else 1 << int(math.ceil(math.log2(max(samples, 2))))
    )
    frequencies = np.fft.rfftfreq(fft_samples, 1.0 / sample_rate_hz)
    columns = []
    for start in starts:
        transformed = np.fft.rfft(values[start : start + samples] * window, n=fft_samples)
        columns.append(np.abs(transformed) ** 2)
    power = np.column_stack(columns)
    centers = starts + samples // 2
    times = time_s[np.minimum(centers, time_s.size - 1)]
    return times, frequencies, power, {
        "window_us": samples / sample_rate_hz * 1e6,
        "overlap_ratio": overlap_ratio,
        "fft_samples": fft_samples,
        "time_step_us": hop / sample_rate_hz * 1e6,
        "frequency_bin_hz": sample_rate_hz / fft_samples,
        "window_resolution_hz": sample_rate_hz / samples,
    }


WAVELET_FILTERS = {
    "haar": np.array([1 / math.sqrt(2), 1 / math.sqrt(2)], dtype=np.float64),
    "db4": np.array(
        [
            -0.010597401785069032,
            0.0328830116668852,
            0.030841381835560764,
            -0.18703481171888114,
            -0.027983769416859854,
            0.6308807679298587,
            0.7148465705529154,
            0.2303778133088964,
        ],
        dtype=np.float64,
    ),
}


def _packet_children(values: np.ndarray, low_filter: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    high_filter = np.array(
        [((-1.0) ** index) * low_filter[-1 - index] for index in range(low_filter.size)]
    )
    low = np.zeros(values.size, dtype=np.float64)
    high = np.zeros(values.size, dtype=np.float64)
    for index, coefficient in enumerate(low_filter):
        low += coefficient * np.roll(values, index)
    for index, coefficient in enumerate(high_filter):
        high += coefficient * np.roll(values, index)
    return low[::2], high[::2]


def wpd_map(
    time_s: np.ndarray,
    values: np.ndarray,
    sample_rate_hz: float,
    raw: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    level = int(raw.get("wpd_level", 0))
    wavelet = str(raw.get("wavelet", "db4"))
    if wavelet not in WAVELET_FILTERS:
        raise ValueError("WPD小波只支持 haar 或 db4")
    maximum_level = max(1, int(math.floor(math.log2(max(values.size / 16, 1)))))
    maximum_level = min(12, maximum_level)
    if level == 0:
        level = maximum_level
    if not 1 <= level <= maximum_level:
        raise ValueError(f"当前数据的WPD层数必须在1到{maximum_level}之间，0表示自动")
    nodes: dict[str, np.ndarray] = {"": values}
    for _ in range(level):
        next_nodes: dict[str, np.ndarray] = {}
        for path, node in nodes.items():
            approximation, detail = _packet_children(node, WAVELET_FILTERS[wavelet])
            next_nodes[path + "0"] = approximation
            next_nodes[path + "1"] = detail
        nodes = next_nodes
    gray_paths = [
        format(index ^ (index >> 1), f"0{level}b")
        for index in range(2**level)
    ]
    ordered = [nodes[path] for path in gray_paths]
    coefficient_samples = min(max(len(node) for node in ordered), 800)
    times = np.linspace(time_s[0], time_s[-1], coefficient_samples)
    rows = []
    for node in ordered:
        source_x = np.linspace(0.0, 1.0, node.size)
        target_x = np.linspace(0.0, 1.0, coefficient_samples)
        rows.append(np.interp(target_x, source_x, np.abs(node) ** 2))
    power = np.asarray(rows, dtype=np.float64)
    band_width = sample_rate_hz / 2.0 / (2**level)
    frequencies = (np.arange(2**level, dtype=np.float64) + 0.5) * band_width
    return times, frequencies, power, {
        "wpd_level": level,
        "wavelet": wavelet,
        "bands": 2**level,
        "band_width_hz": band_width,
    }


def cwt_map(
    time_s: np.ndarray,
    values: np.ndarray,
    sample_rate_hz: float,
    raw: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    low_hz = float(raw.get("frequency_min_hz", 20_000.0))
    high_hz = float(raw.get("frequency_max_hz", 180_000.0))
    bins = int(raw.get("cwt_bins", 72))
    omega0 = float(raw.get("morlet_omega0", 6.0))
    logarithmic = bool(raw.get("cwt_log_frequency", False))
    nyquist = sample_rate_hz / 2.0
    if not 0 < low_hz < high_hz < nyquist:
        raise ValueError(f"CWT频率必须满足 0 < 下限 < 上限 < {nyquist:g} Hz")
    if not 16 <= bins <= 160:
        raise ValueError("CWT频率点数必须在16到160之间")
    if not 4.0 <= omega0 <= 12.0:
        raise ValueError("Morlet ω0必须在4到12之间")
    frequencies = (
        np.geomspace(low_hz, high_hz, bins)
        if logarithmic
        else np.linspace(low_hz, high_hz, bins)
    )
    angular = 2.0 * np.pi * np.fft.fftfreq(values.size, 1.0 / sample_rate_hz)
    transformed = np.fft.fft(values)
    rows = []
    for frequency in frequencies:
        scale_s = omega0 / (2.0 * np.pi * frequency)
        response = np.exp(-0.5 * (scale_s * angular - omega0) ** 2)
        response[angular <= 0] = 0.0
        coefficient = np.fft.ifft(transformed * response)
        rows.append(np.abs(coefficient) ** 2)
    power = np.asarray(rows, dtype=np.float64)
    limited_time, power = _limit_axis(time_s, power, 800, axis=1)
    return limited_time, frequencies, power, {
        "cwt_bins": bins,
        "morlet_omega0": omega0,
        "log_frequency": logarithmic,
    }


def time_frequency_map(
    time_s: np.ndarray,
    values: np.ndarray,
    raw: dict[str, Any],
) -> dict[str, Any]:
    method = str(raw.get("method", "stft")).lower()
    start_us = float(raw.get("start_us", time_s[0] * 1e6))
    end_us = float(raw.get("end_us", time_s[-1] * 1e6))
    local_time, local_values, sample_rate_hz = _segment(
        time_s, values, start_us, end_us
    )
    if method == "stft":
        times, frequencies, power, details = stft_map(
            local_time, local_values, sample_rate_hz, raw
        )
    elif method == "wpd":
        times, frequencies, power, details = wpd_map(
            local_time, local_values, sample_rate_hz, raw
        )
    elif method == "cwt":
        times, frequencies, power, details = cwt_map(
            local_time, local_values, sample_rate_hz, raw
        )
    else:
        raise ValueError("时频方法必须是 stft、wpd 或 cwt")
    minimum_hz = max(0.0, float(raw.get("frequency_min_hz", 0.0)))
    maximum_hz = min(
        sample_rate_hz / 2.0,
        float(raw.get("frequency_max_hz", sample_rate_hz / 2.0)),
    )
    allowed = (frequencies >= minimum_hz) & (frequencies <= maximum_hz)
    if not np.any(allowed):
        raise ValueError("所选频率范围内没有时频数据")
    frequencies = frequencies[allowed]
    power = power[allowed]
    frequencies, power = _limit_axis(frequencies, power, 256, axis=0)
    times, power = _limit_axis(times, power, 800, axis=1)
    floor_db = float(raw.get("floor_db", -60.0))
    if not -120.0 <= floor_db <= -10.0:
        raise ValueError("时频图动态范围下限必须在-120到-10 dB之间")
    return {
        "method": method,
        "time_us": (times * 1e6).tolist(),
        "frequency_hz": frequencies.tolist(),
        "values_db": _to_relative_db(power, floor_db).tolist(),
        "floor_db": floor_db,
        "details": details,
    }
