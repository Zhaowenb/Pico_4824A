"""Frequency/cycle sweep execution, scoring, and durable result storage."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import threading
from typing import Any, Callable

import numpy as np

from .config import AcquisitionConfig
from .device import CaptureResult, Pico4824A
from .storage_naming import session_directory
from .storage import save_npz


SWEEP_MODES = {"frequency", "cycles", "grid"}


@dataclass(slots=True)
class SweepConfig:
    """Validated sweep axes and adaptive N/f waveform scoring parameters."""

    mode: str = "frequency"
    frequency_start_hz: float = 60_000.0
    frequency_stop_hz: float = 100_000.0
    frequency_step_hz: float = 10_000.0
    cycles_start: int = 5
    cycles_stop: int = 15
    cycles_step: int = 1
    repeats: int = 3
    interval_s: float = 1.0
    metric_band_low_hz: float = 20_000.0
    metric_band_high_hz: float = 180_000.0
    direct_search_start_us: float = 90.0
    direct_search_end_us: float = 440.0
    reflection_delay_min_us: float = 420.0
    reflection_delay_max_us: float = 570.0
    packet_window_scale: float = 1.0
    tail_guard_after_cycles: float = 0.0
    tail_guard_before_cycles: float = 0.0
    noise_start_us: float = -95.0
    noise_end_us: float = -20.0
    settling_threshold_ratio: float = 0.10
    settling_hold_cycles: float = 2.0
    recommendation_max_duration_us: float = 100.0
    recommendation_min_strength_db: float = -12.0
    recommendation_strong_min_strength_db: float = -6.0
    recommendation_min_reflection_snr_db: float = 25.0
    receiver_channels: tuple[str, ...] = tuple("ABCDEFG")
    transmitter_channel: str = "H"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SweepConfig":
        values = dict(raw)
        if "receiver_channels" in values:
            values["receiver_channels"] = tuple(
                dict.fromkeys(str(name).upper() for name in values["receiver_channels"])
            )
        # Accept old saved/Web payloads without allowing their fixed windows to
        # affect the new adaptive evaluator.
        for obsolete in (
            "direct_start_us", "direct_end_us", "tail_start_us", "tail_end_us",
            "reflection_start_us", "reflection_end_us",
        ):
            values.pop(obsolete, None)
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        self.mode = self.mode.lower()
        if self.mode not in SWEEP_MODES:
            raise ValueError("扫描模式必须是 frequency、cycles 或 grid")
        if not 0 < self.frequency_start_hz <= self.frequency_stop_hz <= 1_000_000:
            raise ValueError("扫频范围必须在 (0, 1 MHz] 且起点不大于终点")
        if self.frequency_step_hz <= 0:
            raise ValueError("频率步长必须大于 0")
        if not 1 <= self.cycles_start <= self.cycles_stop:
            raise ValueError("周期范围必须为正整数且起点不大于终点")
        if self.cycles_step < 1:
            raise ValueError("周期步长必须至少为 1")
        if not 1 <= self.repeats <= 100:
            raise ValueError("每点重复次数必须在 1 到 100 之间")
        if not 0 <= self.interval_s <= 3600:
            raise ValueError("采集间隔必须在 0 到 3600 秒之间")
        if not 0 < self.metric_band_low_hz < self.metric_band_high_hz <= 5_000_000:
            raise ValueError("评价频带必须满足 0 < 下限 < 上限 ≤ 5 MHz")
        if not 0 <= self.direct_search_start_us < self.direct_search_end_us:
            raise ValueError("直达波搜索范围必须满足 0 ≤ 起点 < 终点")
        if not 0 < self.reflection_delay_min_us < self.reflection_delay_max_us:
            raise ValueError("反射延迟搜索范围必须满足 0 < 最小值 < 最大值")
        if not 0.25 <= self.packet_window_scale <= 3.0:
            raise ValueError("N/f 主波包窗倍率必须在 0.25 到 3.0 之间")
        if not 0 <= self.tail_guard_after_cycles <= 20 or not 0 <= self.tail_guard_before_cycles <= 20:
            raise ValueError("拖尾窗两侧保护周期必须在 0 到 20 之间")
        if self.noise_end_us <= self.noise_start_us:
            raise ValueError("噪声窗必须满足起点 < 终点")
        if not 0 < self.settling_threshold_ratio < 1:
            raise ValueError("稳定阈值比例必须在 0 到 1 之间")
        if not 0.25 <= self.settling_hold_cycles <= 20:
            raise ValueError("稳定保持周期必须在 0.25 到 20 之间")
        if self.recommendation_max_duration_us <= 0:
            raise ValueError("推荐方案最大 N/f 时长必须大于 0")
        if self.recommendation_min_strength_db > 0 or self.recommendation_strong_min_strength_db > 0:
            raise ValueError("相对信号强度阈值必须不大于 0 dB")
        if self.recommendation_min_reflection_snr_db < 0:
            raise ValueError("端面反射 SNR 阈值不能为负数")
        self.receiver_channels = tuple(
            dict.fromkeys(str(name).upper() for name in self.receiver_channels)
        )
        self.transmitter_channel = str(self.transmitter_channel or "NONE").upper()
        if self.transmitter_channel not in {*set("ABCDEFGH"), "NONE"}:
            raise ValueError("发射监测通道必须是 A 到 H，或选择 NONE")
        if not self.receiver_channels or not set(self.receiver_channels) <= set("ABCDEFGH"):
            raise ValueError("最终评价接收通道必须从 A 到 H 中至少选择一个")
        if self.transmitter_channel != "NONE" and self.transmitter_channel in self.receiver_channels:
            raise ValueError("发射监测通道不能同时作为最终评价接收通道")
        if self.total_runs > 10_000:
            raise ValueError("一次扫描最多允许 10,000 次采集，请缩小范围或重复次数")

    @staticmethod
    def _float_axis(start: float, stop: float, step: float) -> tuple[float, ...]:
        count = int(math.floor((stop - start) / step + 1e-9)) + 1
        values = [start + index * step for index in range(count)]
        if values[-1] < stop - max(1e-9, abs(stop) * 1e-12):
            values.append(stop)
        return tuple(float(min(value, stop)) for value in values)

    @property
    def frequencies_hz(self) -> tuple[float, ...]:
        return self._float_axis(
            self.frequency_start_hz, self.frequency_stop_hz, self.frequency_step_hz
        )

    @property
    def cycles_values(self) -> tuple[int, ...]:
        values = list(range(self.cycles_start, self.cycles_stop + 1, self.cycles_step))
        if values[-1] != self.cycles_stop:
            values.append(self.cycles_stop)
        return tuple(values)

    def points(self, base: AcquisitionConfig) -> tuple[tuple[float, int], ...]:
        frequencies = (
            self.frequencies_hz
            if self.mode in {"frequency", "grid"}
            else (float(base.awg.frequency_hz),)
        )
        cycles = (
            self.cycles_values
            if self.mode in {"cycles", "grid"}
            else (int(base.awg.cycles),)
        )
        return tuple((frequency, cycle) for cycle in cycles for frequency in frequencies)

    @property
    def point_count(self) -> int:
        frequencies = len(self.frequencies_hz) if self.mode in {"frequency", "grid"} else 1
        cycles = len(self.cycles_values) if self.mode in {"cycles", "grid"} else 1
        return frequencies * cycles

    @property
    def total_runs(self) -> int:
        return self.point_count * self.repeats


@dataclass(slots=True)
class SweepOutcome:
    directory: Path
    run_rows: list[dict[str, Any]]
    summary_rows: list[dict[str, Any]]
    stopped: bool

    def payload(self) -> dict[str, Any]:
        receiver_channels = (
            str(self.run_rows[0].get("receiver_channels", "")).split("/")
            if self.run_rows
            else []
        )
        return {
            "directory": str(self.directory),
            "runs_csv": str(self.directory / "runs.csv"),
            "summary_csv": str(self.directory / "summary.csv"),
            "run_rows": self.run_rows,
            "summary_rows": self.summary_rows,
            "receiver_channels": [name for name in receiver_channels if name],
            "stopped": self.stopped,
        }


def _db_ratio(numerator: float, denominator: float, *, amplitude: bool = True) -> float:
    factor = 20.0 if amplitude else 10.0
    return factor * math.log10(max(numerator, 1e-30) / max(denominator, 1e-30))


def _moving_rms(values: np.ndarray, samples: int) -> np.ndarray:
    samples = max(3, min(int(samples) | 1, values.size - 1))
    squared = values * values
    cumulative = np.concatenate(([0.0], np.cumsum(squared, dtype=np.float64)))
    valid = np.sqrt(np.maximum((cumulative[samples:] - cumulative[:-samples]) / samples, 0.0))
    left = samples // 2
    return np.pad(valid, (left, values.size - valid.size - left), mode="edge")


def _baseline_center(values: np.ndarray, time_us: np.ndarray) -> np.ndarray:
    pretrigger = values[time_us < 0.0]
    if pretrigger.size < 20:
        pretrigger = values[: max(20, round(values.size * 0.08))]
    return values - float(np.median(pretrigger))


def _carrier_signal(
    values: np.ndarray,
    time_us: np.ndarray,
    sample_rate_hz: float,
    frequency_hz: float,
    cycles: int,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Frequency-aware detector for a finite Hann burst."""
    centered = _baseline_center(values, time_us)
    frequencies = np.fft.rfftfreq(values.size, 1.0 / sample_rate_hz)
    pass_half = max(0.12 * frequency_hz, 1.5 * frequency_hz / cycles)
    stop_half = max(0.22 * frequency_hz, 2.5 * frequency_hz / cycles)
    distance = np.abs(frequencies - frequency_hz)
    weights = np.zeros_like(frequencies)
    weights[distance <= pass_half] = 1.0
    transition = (distance > pass_half) & (distance < stop_half)
    weights[transition] = 0.5 * (
        1.0 + np.cos(np.pi * (distance[transition] - pass_half) / (stop_half - pass_half))
    )
    filtered = np.fft.irfft(np.fft.rfft(centered) * weights, n=values.size)
    envelope = _moving_rms(filtered, round(1.5 * sample_rate_hz / frequency_hz))
    return filtered, envelope, frequency_hz - stop_half, frequency_hz + stop_half


def _metric_signal(
    values: np.ndarray,
    time_us: np.ndarray,
    sample_rate_hz: float,
    low_hz: float,
    high_hz: float,
) -> np.ndarray:
    """Fixed physical band used for comparable RMS metrics across all sweep points."""
    if high_hz >= sample_rate_hz / 2.0:
        raise ValueError(
            f"评价频带上限 {high_hz:g} Hz 必须低于奈奎斯特频率 {sample_rate_hz / 2:g} Hz"
        )
    centered = _baseline_center(values, time_us)
    frequencies = np.fft.rfftfreq(values.size, 1.0 / sample_rate_hz)
    weights = np.zeros_like(frequencies)
    weights[(frequencies >= low_hz) & (frequencies <= high_hz)] = 1.0
    lower = (frequencies >= low_hz * 0.5) & (frequencies < low_hz)
    weights[lower] = 0.5 * (
        1.0 - np.cos(np.pi * (frequencies[lower] - low_hz * 0.5) / (low_hz * 0.5))
    )
    upper = (frequencies > high_hz) & (frequencies <= high_hz * 1.25)
    weights[upper] = 0.5 * (
        1.0 + np.cos(np.pi * (frequencies[upper] - high_hz) / (high_hz * 0.25))
    )
    return np.fft.irfft(np.fft.rfft(centered) * weights, n=values.size)


def _peak_time(time_us: np.ndarray, envelope: np.ndarray, start_us: float, end_us: float) -> float:
    mask = (time_us >= start_us) & (time_us <= end_us)
    if not np.any(mask):
        raise ValueError(f"峰值搜索范围 {start_us:g}–{end_us:g} µs 超出采集记录")
    indices = np.flatnonzero(mask)
    return float(time_us[indices[np.argmax(envelope[indices])]])


def _window_metrics(
    signals: dict[str, np.ndarray], time_us: np.ndarray, start_us: float, end_us: float
) -> tuple[float, float, float, dict[str, float]]:
    mask = (time_us >= start_us) & (time_us < end_us)
    if np.count_nonzero(mask) < 10:
        raise ValueError(f"评价窗 {start_us:.3f}–{end_us:.3f} µs 样本不足")
    channel_rms = {
        name: float(np.sqrt(np.mean(values[mask] ** 2))) for name, values in signals.items()
    }
    combined = np.sqrt(np.mean(np.vstack([values[mask] ** 2 for values in signals.values()]), axis=0))
    rms = float(np.sqrt(np.mean(combined**2)))
    peak = float(np.max(combined))
    dt_us = float(np.median(np.diff(time_us)))
    energy = float(np.sum(combined**2) * dt_us)
    return rms, peak, energy, channel_rms


def _settling_delay_us(
    time_us: np.ndarray,
    envelope: np.ndarray,
    direct_peak_us: float,
    direct_end_us: float,
    reflection_start_us: float,
    frequency_hz: float,
    threshold_ratio: float,
    hold_cycles: float,
) -> tuple[float, float]:
    direct_near = (time_us >= direct_peak_us - 0.5e6 / frequency_hz) & (
        time_us <= direct_peak_us + 0.5e6 / frequency_hz
    )
    peak = float(np.max(envelope[direct_near]))
    indices = np.flatnonzero((time_us >= direct_end_us) & (time_us < reflection_start_us))
    maximum_delay = max(0.0, reflection_start_us - direct_end_us)
    if indices.size == 0:
        return maximum_delay, 0.0
    dt_s = float(np.median(np.diff(time_us))) * 1e-6
    persistence = max(3, round(hold_cycles / frequency_hz / dt_s))
    below = envelope[indices] <= peak * threshold_ratio
    if below.size < persistence:
        return maximum_delay, 0.0
    run = np.convolve(below.astype(np.int16), np.ones(persistence, dtype=np.int16), mode="valid")
    hits = np.flatnonzero(run == persistence)
    if hits.size == 0:
        return maximum_delay, 0.0
    return max(0.0, float(time_us[indices[int(hits[0])]]) - direct_end_us), 1.0


def capture_metrics(result: CaptureResult, sweep: SweepConfig) -> dict[str, float]:
    """Evaluate one capture with adaptive peak detection and strict N/f windows.

    The receiver channel set is explicit and may use any enabled input except
    the transmitter-monitor channel.
    """
    sweep.validate()
    time_us = np.asarray(result.time_s, dtype=np.float64) * 1e6
    sample_rate_hz = float(result.actual_sample_rate_hz)
    frequency_hz = float(result.config.awg.frequency_hz)
    cycles = int(result.config.awg.cycles)
    raw = {
        name: np.asarray(result.volts[name], dtype=np.float64)
        for name in sweep.receiver_channels
        if name in result.volts
    }
    if not raw:
        raise ValueError(
            "配置的最终评价接收通道均未启用；请检查扫描评价通道与采集通道"
        )

    carrier: dict[str, np.ndarray] = {}
    metric: dict[str, np.ndarray] = {}
    envelopes: dict[str, np.ndarray] = {}
    band_low_hz = band_high_hz = 0.0
    for name, values in raw.items():
        carrier[name], envelopes[name], band_low_hz, band_high_hz = _carrier_signal(
            values, time_us, sample_rate_hz, frequency_hz, cycles
        )
        metric[name] = _metric_signal(
            values, time_us, sample_rate_hz,
            sweep.metric_band_low_hz, sweep.metric_band_high_hz,
        )
    combined_envelope = np.sqrt(
        np.mean(np.vstack([values**2 for values in envelopes.values()]), axis=0)
    )

    direct_peak_us = _peak_time(
        time_us, combined_envelope, sweep.direct_search_start_us, sweep.direct_search_end_us
    )
    reflection_search_start = direct_peak_us + sweep.reflection_delay_min_us
    reflection_search_end = min(
        direct_peak_us + sweep.reflection_delay_max_us, float(time_us[-1])
    )
    reflection_peak_us = _peak_time(
        time_us, combined_envelope, reflection_search_start, reflection_search_end
    )

    nominal_duration_us = cycles / frequency_hz * 1e6
    one_cycle_us = 1e6 / frequency_hz
    packet_width_us = nominal_duration_us * sweep.packet_window_scale
    direct_start_us = direct_peak_us - packet_width_us / 2.0
    direct_end_us = direct_peak_us + packet_width_us / 2.0
    reflection_start_us = reflection_peak_us - packet_width_us / 2.0
    reflection_end_us = reflection_peak_us + packet_width_us / 2.0
    tail_start_us = direct_end_us + sweep.tail_guard_after_cycles * one_cycle_us
    tail_end_us = reflection_start_us - sweep.tail_guard_before_cycles * one_cycle_us
    if tail_end_us <= tail_start_us:
        raise ValueError(
            "N/f 主波包窗和拖尾保护周期占满了直达波到端面反射的间隔；"
            "请减小窗倍率或保护周期"
        )

    direct_rms, direct_peak, direct_energy, direct_channels = _window_metrics(
        metric, time_us, direct_start_us, direct_end_us
    )
    tail_rms, tail_peak, tail_energy, tail_channels = _window_metrics(
        metric, time_us, tail_start_us, tail_end_us
    )
    reflection_rms, reflection_peak, reflection_energy, reflection_channels = _window_metrics(
        metric, time_us, reflection_start_us, reflection_end_us
    )
    noise_rms, _, _, noise_channels = _window_metrics(
        metric, time_us, sweep.noise_start_us, sweep.noise_end_us
    )
    carrier_direct_rms, _, _, _ = _window_metrics(
        carrier, time_us, direct_start_us, direct_end_us
    )
    carrier_tail_rms, _, _, _ = _window_metrics(
        carrier, time_us, tail_start_us, tail_end_us
    )
    carrier_reflection_rms, _, _, _ = _window_metrics(
        carrier, time_us, reflection_start_us, reflection_end_us
    )
    settling_us, settling_reached = _settling_delay_us(
        time_us, combined_envelope, direct_peak_us, direct_end_us, reflection_start_us,
        frequency_hz, sweep.settling_threshold_ratio, sweep.settling_hold_cycles,
    )

    metrics: dict[str, float] = {
        "nominal_duration_us": nominal_duration_us,
        "one_cycle_us": one_cycle_us,
        "resolution_bandwidth_proxy_hz": frequency_hz / cycles,
        "packet_window_width_us": packet_width_us,
        "carrier_band_low_hz": band_low_hz,
        "carrier_band_high_hz": band_high_hz,
        "direct_peak_us": direct_peak_us,
        "direct_start_us": direct_start_us,
        "direct_end_us": direct_end_us,
        "tail_start_us": tail_start_us,
        "tail_end_us": tail_end_us,
        "tail_duration_us": tail_end_us - tail_start_us,
        "reflection_peak_us": reflection_peak_us,
        "reflection_start_us": reflection_start_us,
        "reflection_end_us": reflection_end_us,
        "flight_delay_us": reflection_peak_us - direct_peak_us,
        "receiver_direct_rms_v": direct_rms,
        "receiver_tail_rms_v": tail_rms,
        "receiver_reflection_rms_v": reflection_rms,
        "receiver_noise_rms_v": noise_rms,
        "receiver_direct_peak_v": direct_peak,
        "receiver_tail_peak_v": tail_peak,
        "receiver_reflection_peak_v": reflection_peak,
        "direct_energy_v2_us": direct_energy,
        "tail_energy_v2_us": tail_energy,
        "reflection_energy_v2_us": reflection_energy,
        "tail_to_direct_db": _db_ratio(tail_rms, direct_rms),
        "reflection_to_direct_db": _db_ratio(reflection_rms, direct_rms),
        "tail_peak_to_direct_peak_db": _db_ratio(tail_peak, direct_peak),
        "tail_energy_to_direct_energy_db": _db_ratio(
            tail_energy, direct_energy, amplitude=False
        ),
        "quality_db": _db_ratio(math.sqrt(direct_rms * reflection_rms), tail_rms),
        "direct_snr_db": _db_ratio(direct_rms, noise_rms),
        "reflection_snr_db": _db_ratio(reflection_rms, noise_rms),
        "tail_to_noise_db": _db_ratio(tail_rms, noise_rms),
        "signal_strength_v": math.sqrt(direct_rms * reflection_rms),
        "carrier_direct_rms_v": carrier_direct_rms,
        "carrier_tail_rms_v": carrier_tail_rms,
        "carrier_reflection_rms_v": carrier_reflection_rms,
        "carrier_tail_to_direct_db": _db_ratio(carrier_tail_rms, carrier_direct_rms),
        "settling_to_threshold_us": settling_us,
        "settling_threshold_reached": settling_reached,
    }
    for name in raw:
        metrics[f"{name}_direct_rms_v"] = direct_channels[name]
        metrics[f"{name}_tail_rms_v"] = tail_channels[name]
        metrics[f"{name}_reflection_rms_v"] = reflection_channels[name]
        metrics[f"{name}_noise_rms_v"] = noise_channels[name]
        metrics[f"{name}_tail_to_direct_db"] = _db_ratio(
            tail_channels[name], direct_channels[name]
        )
        metrics[f"{name}_reflection_to_direct_db"] = _db_ratio(
            reflection_channels[name], direct_channels[name]
        )

    transmitter = result.volts.get(sweep.transmitter_channel)
    if transmitter is not None:
        centered = _baseline_center(np.asarray(transmitter, dtype=np.float64), time_us)
        start = max(float(time_us[0]), -0.2 * nominal_duration_us)
        end = min(float(time_us[-1]), 1.3 * nominal_duration_us)
        mask = (time_us >= start) & (time_us < end)
        if np.count_nonzero(mask) >= 10:
            metrics["transmit_rms_v"] = float(np.sqrt(np.mean(centered[mask] ** 2)))
    return metrics


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _rank_rows(
    rows: list[dict[str, Any]], key: str, rank_key: str, *, reverse: bool,
    eligible: Callable[[dict[str, Any]], bool] | None = None,
) -> None:
    for row in rows:
        row[rank_key] = 0
    candidates = [row for row in rows if eligible is None or eligible(row)]
    candidates.sort(key=lambda row: float(row[key]), reverse=reverse)
    for rank, row in enumerate(candidates, start=1):
        row[rank_key] = rank


def _enrich_summary_rows(rows: list[dict[str, Any]], sweep: SweepConfig | None) -> None:
    if not rows:
        return
    max_strength = max(float(row.get("signal_strength_v", 0.0)) for row in rows)
    for row in rows:
        strength = float(row.get("signal_strength_v", 0.0))
        row["strength_db_from_max"] = _db_ratio(strength, max_strength)
        row["duration_eligible"] = 0
        row["balanced_eligible"] = 0
        row["strong_eligible"] = 0
        row["recommendation"] = ""
    _rank_rows(rows, "tail_to_direct_db", "tail_rank_all", reverse=False)
    if sweep is None:
        return

    def duration_ok(row: dict[str, Any]) -> bool:
        return float(row["nominal_duration_us"]) <= sweep.recommendation_max_duration_us

    def balanced_ok(row: dict[str, Any]) -> bool:
        return (
            duration_ok(row)
            and float(row["strength_db_from_max"]) >= sweep.recommendation_min_strength_db
            and float(row["reflection_snr_db"]) >= sweep.recommendation_min_reflection_snr_db
        )

    def strong_ok(row: dict[str, Any]) -> bool:
        return (
            duration_ok(row)
            and float(row["strength_db_from_max"])
            >= sweep.recommendation_strong_min_strength_db
            and float(row["reflection_snr_db"]) >= sweep.recommendation_min_reflection_snr_db
        )

    for row in rows:
        row["duration_eligible"] = int(duration_ok(row))
        row["balanced_eligible"] = int(balanced_ok(row))
        row["strong_eligible"] = int(strong_ok(row))
    _rank_rows(
        rows, "tail_to_direct_db", "duration_tail_rank", reverse=False, eligible=duration_ok
    )
    _rank_rows(
        rows, "quality_db", "balanced_quality_rank", reverse=True, eligible=balanced_ok
    )
    _rank_rows(
        rows, "tail_to_direct_db", "strong_tail_rank", reverse=False, eligible=strong_ok
    )
    recommended = next((row for row in rows if row["balanced_quality_rank"] == 1), None)
    if recommended is None:
        recommended = next((row for row in rows if row["duration_tail_rank"] == 1), None)
    if recommended is not None:
        recommended["recommendation"] = "balanced"


def aggregate_rows(
    run_rows: list[dict[str, Any]], sweep: SweepConfig | None = None
) -> list[dict[str, Any]]:
    groups: dict[tuple[float, int], list[dict[str, Any]]] = {}
    for row in run_rows:
        key = (float(row["frequency_hz"]), int(row["cycles"]))
        groups.setdefault(key, []).append(row)
    summary: list[dict[str, Any]] = []
    fixed = {"run_index", "point_index", "repeat", "frequency_hz", "cycles", "npz_file"}
    for (frequency, cycles), rows in groups.items():
        item: dict[str, Any] = {
            "frequency_hz": frequency,
            "cycles": cycles,
            "repeats_completed": len(rows),
        }
        numeric_keys = [
            key
            for key, value in rows[0].items()
            if key not in fixed and isinstance(value, (int, float))
        ]
        for key in numeric_keys:
            values = np.asarray([float(row[key]) for row in rows], dtype=np.float64)
            item[key] = float(np.mean(values))
            item[f"{key}_std"] = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
        summary.append(item)
    _enrich_summary_rows(summary, sweep)
    return summary


ProgressCallback = Callable[[dict[str, Any], CaptureResult | None], None]


def execute_sweep(
    base_config: AcquisitionConfig,
    sweep: SweepConfig,
    device: Pico4824A,
    output_root: Path,
    stop_event: threading.Event,
    progress: ProgressCallback | None = None,
) -> SweepOutcome:
    """Run all requested points using one open device and save every repetition."""
    base_config.validate()
    sweep.validate()
    if not base_config.awg.enabled:
        raise ValueError("自动扫描需要启用 AWG")
    enabled_receivers = [
        name for name in sweep.receiver_channels if base_config.channels[name].enabled
    ]
    disabled_receivers = [
        name for name in sweep.receiver_channels if not base_config.channels[name].enabled
    ]
    if disabled_receivers:
        raise ValueError(
            "以下评价通道尚未在采集设置中启用："
            + ",".join(disabled_receivers)
        )
    if not enabled_receivers:
        raise ValueError(
            "自动评价至少需要启用一个配置的接收通道；"
            f"当前评价通道为 {','.join(sweep.receiver_channels)}"
        )
    if (
        sweep.transmitter_channel != "NONE"
        and not base_config.channels[sweep.transmitter_channel].enabled
    ):
        raise ValueError(
            f"发射监测通道 {sweep.transmitter_channel} 尚未在采集设置中启用"
        )
    earliest_time_us = -base_config.pre_trigger_samples / base_config.sample_rate_hz * 1e6
    latest_time_us = (base_config.post_trigger_samples - 1) / base_config.sample_rate_hz * 1e6
    if sweep.noise_start_us < earliest_time_us:
        raise ValueError(
            f"噪声窗起点 {sweep.noise_start_us:g} µs 早于采集起点 {earliest_time_us:.3f} µs；"
            "请增加触发前点数或后移噪声窗"
        )
    if latest_time_us < sweep.direct_search_start_us + sweep.reflection_delay_min_us:
        raise ValueError(
            f"采集只记录到约 {latest_time_us:.3f} µs，不能覆盖直达波后至少 "
            f"{sweep.reflection_delay_min_us:g} µs 的端面反射搜索；请增加触发后点数"
        )

    directory = session_directory(output_root, "sweep", simulated=getattr(device, "simulate", False))
    raw_dir = directory / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    (directory / "sweep_config.json").write_text(
        json.dumps(
            {"base_config": base_config.to_dict(), "sweep": asdict(sweep)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    run_rows: list[dict[str, Any]] = []
    total_runs = len(sweep.points(base_config)) * sweep.repeats
    run_index = 0
    for point_index, (frequency_hz, cycles) in enumerate(sweep.points(base_config), start=1):
        for repeat in range(1, sweep.repeats + 1):
            if stop_event.is_set():
                summary = aggregate_rows(run_rows, sweep)
                _write_csv(directory / "runs.csv", run_rows)
                _write_csv(directory / "summary.csv", summary)
                return SweepOutcome(directory, run_rows, summary, stopped=True)

            run_index += 1
            config = AcquisitionConfig.from_dict(base_config.to_dict())
            config.awg.frequency_hz = frequency_hz
            config.awg.cycles = cycles
            config.validate()
            if progress:
                progress(
                    {
                        "phase": "capturing",
                        "run_index": run_index,
                        "total_runs": total_runs,
                        "point_index": point_index,
                        "total_points": sweep.point_count,
                        "repeat": repeat,
                        "repeats": sweep.repeats,
                        "frequency_hz": frequency_hz,
                        "cycles": cycles,
                    },
                    None,
                )
            try:
                result = device.capture(config)
            except Exception:
                if stop_event.is_set():
                    summary = aggregate_rows(run_rows, sweep)
                    _write_csv(directory / "runs.csv", run_rows)
                    _write_csv(directory / "summary.csv", summary)
                    return SweepOutcome(directory, run_rows, summary, stopped=True)
                raise
            file_name = f"频率{frequency_hz/1000:g}kHz__{cycles}周期__重复{repeat:02d}.npz"
            npz_path = save_npz(result, raw_dir / file_name)
            row: dict[str, Any] = {
                "run_index": run_index,
                "point_index": point_index,
                "repeat": repeat,
                "frequency_hz": frequency_hz,
                "cycles": cycles,
                "npz_file": str(npz_path.relative_to(directory)),
                "receiver_channels": "/".join(sweep.receiver_channels),
                **capture_metrics(result, sweep),
            }
            run_rows.append(row)
            summary = aggregate_rows(run_rows, sweep)
            _write_csv(directory / "runs.csv", run_rows)
            _write_csv(directory / "summary.csv", summary)
            if progress:
                progress(
                    {
                        "phase": "saved",
                        "run_index": run_index,
                        "total_runs": total_runs,
                        "point_index": point_index,
                        "total_points": sweep.point_count,
                        "repeat": repeat,
                        "repeats": sweep.repeats,
                        "frequency_hz": frequency_hz,
                        "cycles": cycles,
                    },
                    result,
                )
            if run_index < total_runs and stop_event.wait(sweep.interval_s):
                summary = aggregate_rows(run_rows, sweep)
                return SweepOutcome(directory, run_rows, summary, stopped=True)

    summary = aggregate_rows(run_rows, sweep)
    return SweepOutcome(directory, run_rows, summary, stopped=False)
