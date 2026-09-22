"""Quantify arrival packets and propose sweep scoring windows for one capture."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent / "data" / "capture_20260722_171634.csv"
OUTPUT_JSON = HERE / "window_analysis.json"
OUTPUT_SVG = HERE / "window_analysis.svg"


def analytic_envelope(signal: np.ndarray, sample_rate_hz: float) -> np.ndarray:
    """Band-limit around the excitation and return a smoothed Hilbert envelope."""
    centered = signal - np.median(signal[: max(100, signal.size // 20)])
    spectrum = np.fft.rfft(centered)
    frequency = np.fft.rfftfreq(centered.size, 1.0 / sample_rate_hz)
    transition = np.zeros_like(frequency)
    transition[(frequency >= 35_000.0) & (frequency <= 115_000.0)] = 1.0
    filtered = np.fft.irfft(spectrum * transition, n=centered.size)

    n = filtered.size
    analytic_spectrum = np.fft.fft(filtered)
    multiplier = np.zeros(n)
    multiplier[0] = 1.0
    if n % 2 == 0:
        multiplier[n // 2] = 1.0
        multiplier[1 : n // 2] = 2.0
    else:
        multiplier[1 : (n + 1) // 2] = 2.0
    envelope = np.abs(np.fft.ifft(analytic_spectrum * multiplier))
    smoothing_samples = max(1, round(sample_rate_hz * 10e-6))
    kernel = np.ones(smoothing_samples) / smoothing_samples
    return np.convolve(envelope, kernel, mode="same")


def local_peaks(time_us: np.ndarray, envelope: np.ndarray, start: float, end: float) -> list[dict[str, float]]:
    region = (time_us >= start) & (time_us <= end)
    indices = np.flatnonzero(region)
    if indices.size < 3:
        return []
    values = envelope[indices]
    candidates = indices[1:-1][(values[1:-1] > values[:-2]) & (values[1:-1] >= values[2:])]
    separation = max(1, round(35e-6 / ((time_us[1] - time_us[0]) * 1e-6)))
    selected: list[int] = []
    for index in candidates[np.argsort(envelope[candidates])[::-1]]:
        if all(abs(index - chosen) >= separation for chosen in selected):
            selected.append(int(index))
        if len(selected) == 8:
            break
    return [
        {"time_us": float(time_us[i]), "envelope_v": float(envelope[i])}
        for i in sorted(selected)
    ]


def main() -> None:
    matrix = np.genfromtxt(SOURCE, delimiter=",", names=True)
    time_s = matrix["time_s"]
    time_us = time_s * 1e6
    sample_rate_hz = 1.0 / np.median(np.diff(time_s))
    channels = {name: matrix[f"ch_{name}_v"] for name in "ACGH"}
    envelopes = {name: analytic_envelope(values, sample_rate_hz) for name, values in channels.items()}

    noise = time_us < -20.0
    noise_levels = {
        name: float(np.median(env[noise]) + 6.0 * np.std(env[noise]))
        for name, env in envelopes.items()
    }
    peaks = {name: local_peaks(time_us, env, 0.0, 900.0) for name, env in envelopes.items()}

    combined = np.sqrt(np.mean(np.vstack([envelopes[name] ** 2 for name in "ACG"]), axis=0))
    # Windows intentionally leave guard bands between the first arrival and the ~682 us end reflection.
    windows_us = {
        "direct": [170.0, 335.0],
        "tail": [360.0, 620.0],
        "reflection": [650.0, 800.0],
    }
    metrics: dict[str, dict[str, float]] = {}
    for name in "ACG":
        metrics[name] = {}
        baseline = float(np.mean(channels[name][noise]))
        for label, (start, end) in windows_us.items():
            mask = (time_us >= start) & (time_us < end)
            metrics[name][f"{label}_rms_v"] = float(
                np.sqrt(np.mean((channels[name][mask] - baseline) ** 2))
            )
        metrics[name]["tail_to_direct_db"] = float(
            20.0 * np.log10(
                max(metrics[name]["tail_rms_v"], 1e-15)
                / max(metrics[name]["direct_rms_v"], 1e-15)
            )
        )

    report = {
        "source": str(SOURCE),
        "sample_rate_hz": sample_rate_hz,
        "samples": int(time_s.size),
        "time_span_us": [float(time_us[0]), float(time_us[-1])],
        "noise_threshold_v": noise_levels,
        "prominent_envelope_peaks": peaks,
        "recommended_windows_us": windows_us,
        "window_metrics": metrics,
    }
    OUTPUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    write_svg(time_us, channels, envelopes, windows_us)


def _polyline(values: np.ndarray, x0: float, x1: float, y0: float, height: float) -> str:
    indices = np.linspace(0, values.size - 1, min(2600, values.size), dtype=int)
    subset = values[indices]
    limit = max(float(np.percentile(np.abs(subset), 99.5)), 1e-9)
    xs = x0 + indices / max(values.size - 1, 1) * (x1 - x0)
    ys = y0 + height / 2.0 - np.clip(subset / limit, -1.0, 1.0) * height * 0.40
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys))


def write_svg(
    time_us: np.ndarray,
    channels: dict[str, np.ndarray],
    envelopes: dict[str, np.ndarray],
    windows_us: dict[str, list[float]],
) -> None:
    width, height = 1500, 960
    left, right, top, lane_height = 80, 30, 70, 205
    plot_width = width - left - right
    visible = (time_us >= -100.0) & (time_us <= 900.0)
    visible_time = time_us[visible]
    colors = {"A": "#35d0ba", "C": "#ffb64d", "G": "#73a8ff", "H": "#ff6f96"}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7fafb"/>',
        '<text x="80" y="34" font-family="Segoe UI" font-size="20" fill="#17242c">10-cycle, 70 kHz: direct / tail / end-reflection windows</text>',
    ]
    for lane, name in enumerate("ACGH"):
        y = top + lane * lane_height
        parts.append(f'<rect x="{left}" y="{y}" width="{plot_width}" height="{lane_height - 18}" fill="#ffffff" stroke="#d7e1e5"/>')
        if name != "H":
            shades = {"direct": "#35d0ba", "tail": "#ffb64d", "reflection": "#8a78d1"}
            for label, (start, end) in windows_us.items():
                x = left + (start + 100.0) / 1000.0 * plot_width
                w = (end - start) / 1000.0 * plot_width
                parts.append(f'<rect x="{x:.2f}" y="{y}" width="{w:.2f}" height="{lane_height - 18}" fill="{shades[label]}" opacity="0.10"/>')
        raw_points = _polyline(channels[name][visible], left, left + plot_width, y, lane_height - 18)
        env_points = _polyline(envelopes[name][visible], left, left + plot_width, y, lane_height - 18)
        parts.append(f'<polyline points="{raw_points}" fill="none" stroke="{colors[name]}" stroke-width="1" opacity="0.75"/>')
        parts.append(f'<polyline points="{env_points}" fill="none" stroke="#17242c" stroke-width="1.3"/>')
        parts.append(f'<text x="20" y="{y + 28}" font-family="Consolas" font-size="16" fill="{colors[name]}">CH {name}</text>')
    for tick in range(-100, 901, 100):
        x = left + (tick + 100.0) / 1000.0 * plot_width
        parts.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + 4 * lane_height - 18}" stroke="#82959d" opacity="0.18"/>')
        parts.append(f'<text x="{x:.2f}" y="{height - 32}" text-anchor="middle" font-family="Consolas" font-size="12" fill="#607680">{tick}</text>')
    parts.append(f'<text x="{width / 2}" y="{height - 8}" text-anchor="middle" font-family="Segoe UI" font-size="14" fill="#40545d">Time (µs)</text>')
    parts.append('</svg>')
    OUTPUT_SVG.write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
