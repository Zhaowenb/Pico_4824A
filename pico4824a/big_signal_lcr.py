"""ATA-2021B large-signal LCR measurement.

This module deliberately lives beside :mod:`lcr` instead of changing the
existing small-signal implementation.  The two measurements therefore have
separate configuration, result and output paths while sharing only the
PicoScope capture primitive.
"""

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

from .config import AcquisitionConfig, CHANNEL_NAMES, RANGE_VOLTS
from .device import CaptureResult, Pico4824A
from .storage import save_npz


@dataclass(slots=True)
class BigSignalLcrConfig:
    """User-facing configuration for the ATA-2021B monitor measurement.

    The AWG drives the ATA input.  The two PicoScope channels are monitor
    outputs, so the conversion coefficients are explicit rather than hidden
    in an assumed amplifier gain:

    * ``voltage_monitor_scale_v_per_v``: coil volts per monitor-channel volt;
    * ``current_monitor_scale_a_per_v``: coil amps per monitor-channel volt.
    """

    # ``mode`` selects the frequency-axis spacing (kept for compatibility with
    # existing requests); ``scan_mode`` selects which dimensions are swept.
    mode: str = "single"
    scan_mode: str = "grid"
    frequency_hz: float = 100_000.0
    frequency_start_hz: float = 20_000.0
    frequency_stop_hz: float = 200_000.0
    frequency_step_hz: float = 5_000.0
    points_per_decade: int = 12
    repeats: int = 1
    interval_s: float = 0.2
    awg_drive_vpp: float = 0.5
    awg_vpp_start: float = 0.5
    awg_vpp_stop: float = 0.5
    awg_vpp_step: float = 0.1
    burst_cycles: int = 40
    ramp_cycles: float = 3.0
    analysis_cycles: int = 16
    analysis_guard_cycles: float = 2.0
    voltage_channel: str = "A"
    current_channel: str = "B"
    trigger_signal: str = "voltage"
    voltage_monitor_scale_v_per_v: float = 1.0
    current_monitor_scale_a_per_v: float = 1.0
    current_monitor_polarity: int = 1
    voltage_monitor_offset_v: float = 0.0
    current_monitor_offset_v: float = 0.0
    ata_voltage_gain: float = 1.0
    auto_monitor_range: bool = False
    auto_range_max_retries: int = 2
    trip_detection_enabled: bool = True
    trip_drop_ratio: float = 0.20
    trip_hold_cycles: float = 1.0
    min_monitor_rms_v: float = 0.002
    max_drive_vpp: float = 2.0
    alert_voltage_vpp_v: float = 400.0
    alert_current_peak_a: float = 2.0
    max_frequency_hz: float = 1_000_000.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BigSignalLcrConfig":
        values = dict(raw)
        # Older saved requests used sinusoidal RMS warning thresholds.  Accept
        # them without silently treating RMS values as peak-to-peak or peak.
        legacy_voltage = values.pop("max_voltage_rms_v", None)
        legacy_current = values.pop("max_current_rms_a", None)
        if legacy_voltage is not None:
            values.setdefault("alert_voltage_vpp_v", float(legacy_voltage) * 2 * math.sqrt(2))
        if legacy_current is not None:
            values.setdefault("alert_current_peak_a", float(legacy_current) * math.sqrt(2))
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        self.mode = str(self.mode).lower()
        if self.mode not in {"single", "linear", "log"}:
            raise ValueError("大信号 LCR 模式必须是 single、linear 或 log")
        self.scan_mode = str(self.scan_mode).lower()
        if self.scan_mode not in {"single", "frequency", "voltage", "grid"}:
            raise ValueError("大信号 LCR 扫描方式必须是 single、frequency、voltage 或 grid")
        if not 1.0 <= self.frequency_hz <= 1_000_000.0:
            raise ValueError("大信号 LCR 单点频率必须在 1 Hz 到 1 MHz 之间")
        if not 1.0 <= self.frequency_start_hz <= self.frequency_stop_hz <= 1_000_000.0:
            raise ValueError("大信号 LCR 扫频范围必须在 1 Hz 到 1 MHz 之间")
        if not math.isfinite(float(self.frequency_step_hz)) or self.frequency_step_hz <= 0:
            raise ValueError("大信号 LCR 线性扫频步长必须大于 0")
        if not 1 <= self.points_per_decade <= 200:
            raise ValueError("大信号 LCR 每十倍频程点数必须在 1 到 200 之间")
        if not 1 <= self.repeats <= 100:
            raise ValueError("大信号 LCR 每点重复次数必须在 1 到 100 之间")
        if not 0 <= self.interval_s <= 3600:
            raise ValueError("大信号 LCR 测量间隔必须在 0 到 3600 秒之间")
        if not 0 < self.awg_drive_vpp <= 4.0:
            raise ValueError("ATA 激励的 Pico AWG 输入必须在 0 到 4 Vpp 之间")
        if not 0 < self.awg_vpp_start <= self.awg_vpp_stop <= 4.0:
            raise ValueError("Pico AWG Vpp 扫描范围必须在 0 到 4 Vpp 之间，且停止值不小于起始值")
        if not math.isfinite(float(self.awg_vpp_step)) or self.awg_vpp_step <= 0:
            raise ValueError("Pico AWG Vpp 扫描步长必须大于 0")
        if not 8 <= self.burst_cycles <= 10_000:
            raise ValueError("大信号 LCR 突发周期数必须在 8 到 10000 之间")
        if self.ramp_cycles < 0 or self.ramp_cycles * 2 + 2 >= self.burst_cycles:
            raise ValueError("大信号 LCR 上下沿周期过长，必须保留至少 2 个平顶周期")
        if not 2 <= self.analysis_cycles < self.burst_cycles - 2 * self.ramp_cycles:
            raise ValueError("大信号 LCR 分析周期数必须位于平顶区内")
        if self.analysis_guard_cycles < 0:
            raise ValueError("大信号 LCR 分析保护周期不能为负")
        channels = {
            "电压监测": str(self.voltage_channel).upper(),
            "电流监测": str(self.current_channel).upper(),
        }
        if any(value not in CHANNEL_NAMES for value in channels.values()):
            raise ValueError("大信号 LCR 电压/电流监测通道必须从 A 到 H 中选择")
        if len(set(channels.values())) != 2:
            raise ValueError("大信号 LCR 电压和电流必须使用两个不同通道")
        self.voltage_channel = channels["电压监测"]
        self.current_channel = channels["电流监测"]
        self.trigger_signal = str(self.trigger_signal).lower()
        if self.trigger_signal not in {"voltage", "current"}:
            raise ValueError("大信号 LCR 触发信号必须是 voltage 或 current")
        if self.current_monitor_polarity not in {-1, 1}:
            raise ValueError("Current Monitor 极性必须是 -1 或 +1")
        for label, value in (
            ("电压监测换算系数", self.voltage_monitor_scale_v_per_v),
            ("电流监测换算系数", self.current_monitor_scale_a_per_v),
            ("最大 AWG 输入", self.max_drive_vpp),
            ("线圈电压提醒阈值", self.alert_voltage_vpp_v),
            ("线圈电流提醒阈值", self.alert_current_peak_a),
            ("最大测量频率", self.max_frequency_hz),
            ("ATA 面板电压增益", self.ata_voltage_gain),
            ("Monitor 最低有效 RMS", self.min_monitor_rms_v),
        ):
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{label}必须是大于0的有限数")
        if self.max_frequency_hz > 1_000_000.0:
            raise ValueError("最大测量频率不能超过 1 MHz")
        if self.ata_voltage_gain > 60:
            raise ValueError("ATA-2021B 面板电压增益必须在 0 到 60 之间")
        if not 0 <= self.auto_range_max_retries <= 4:
            raise ValueError("自动量程最多重采次数必须在 0 到 4 之间")
        if not 0.02 <= self.trip_drop_ratio <= 0.8:
            raise ValueError("疑似跳闸掉幅阈值必须在 2% 到 80% 之间")
        if not 1 <= self.trip_hold_cycles <= 20:
            raise ValueError("疑似跳闸持续周期必须在 1 到 20 之间")
        maximum_shots = (
            self.frequency_point_count * self.drive_point_count * self.repeats
            * (self.auto_range_max_retries + 1 if self.auto_monitor_range else 1)
        )
        if maximum_shots > 10_000:
            raise ValueError("考虑自动量程重采后，一次大信号 LCR 扫描最多允许 10000 次激励")
        if max(self.drive_vpps) > self.max_drive_vpp:
            raise ValueError("ATA 激励超过设定的最大 AWG 输入安全限值")
        if max(self.frequencies_hz) > self.max_frequency_hz:
            raise ValueError("测量频率超过 ATA 安全状态中的最大频率")
        # The voltage/current values above are user-selected software alerts,
        # not amplifier ratings or pre-run interlocks.  The frontend reports
        # an estimated voltage warning without disabling acquisition.

    @property
    def frequency_point_count(self) -> int:
        if self.scan_mode not in {"frequency", "grid"} or self.mode == "single":
            return 1
        if self.mode == "linear":
            count = int(math.floor(
                (self.frequency_stop_hz - self.frequency_start_hz) / self.frequency_step_hz + 1e-9
            )) + 1
            last = self.frequency_start_hz + (count - 1) * self.frequency_step_hz
            return count + (last < self.frequency_stop_hz - max(1e-9, abs(self.frequency_stop_hz) * 1e-12))
        decades = math.log10(self.frequency_stop_hz / self.frequency_start_hz)
        return max(2, int(math.ceil(decades * self.points_per_decade)) + 1)

    @property
    def drive_point_count(self) -> int:
        if self.scan_mode not in {"voltage", "grid"}:
            return 1
        count = int(math.floor(
            (self.awg_vpp_stop - self.awg_vpp_start) / self.awg_vpp_step + 1e-9
        )) + 1
        last = self.awg_vpp_start + (count - 1) * self.awg_vpp_step
        return count + (last < self.awg_vpp_stop - max(1e-9, abs(self.awg_vpp_stop) * 1e-12))

    @property
    def frequencies_hz(self) -> tuple[float, ...]:
        if self.scan_mode not in {"frequency", "grid"} or self.mode == "single":
            return (float(self.frequency_hz),)
        if self.mode == "linear":
            count = int(math.floor(
                (self.frequency_stop_hz - self.frequency_start_hz)
                / self.frequency_step_hz
                + 1e-9
            )) + 1
            values = [
                self.frequency_start_hz + index * self.frequency_step_hz
                for index in range(count)
            ]
            if values[-1] < self.frequency_stop_hz - max(1e-9, abs(self.frequency_stop_hz) * 1e-12):
                values.append(self.frequency_stop_hz)
            return tuple(float(min(value, self.frequency_stop_hz)) for value in values)
        decades = math.log10(self.frequency_stop_hz / self.frequency_start_hz)
        count = max(2, int(math.ceil(decades * self.points_per_decade)) + 1)
        return tuple(float(value) for value in np.geomspace(
            self.frequency_start_hz, self.frequency_stop_hz, count
        ))

    @property
    def drive_vpps(self) -> tuple[float, ...]:
        if self.scan_mode not in {"voltage", "grid"}:
            return (float(self.awg_drive_vpp),)
        count = int(math.floor(
            (self.awg_vpp_stop - self.awg_vpp_start) / self.awg_vpp_step + 1e-9
        )) + 1
        values = [self.awg_vpp_start + index * self.awg_vpp_step for index in range(count)]
        if values[-1] < self.awg_vpp_stop - max(1e-9, abs(self.awg_vpp_stop) * 1e-12):
            values.append(self.awg_vpp_stop)
        return tuple(float(min(value, self.awg_vpp_stop)) for value in values)

    @property
    def parameter_points(self) -> tuple[tuple[float, float], ...]:
        """Return the frequency × AWG Vpp scan in deterministic row-major order."""
        return tuple(
            (frequency, drive_vpp)
            for frequency in self.frequencies_hz
            for drive_vpp in self.drive_vpps
        )

    @property
    def trigger_source_channel(self) -> str:
        return self.voltage_channel if self.trigger_signal == "voltage" else self.current_channel


@dataclass(slots=True)
class BigSignalLcrOutcome:
    directory: Path
    run_rows: list[dict[str, Any]]
    summary_rows: list[dict[str, Any]]
    stopped: bool
    safety_tripped: bool = False

    def payload(self) -> dict[str, Any]:
        return _json_safe({
            "directory": str(self.directory),
            "run_rows": self.run_rows,
            "summary_rows": self.summary_rows,
            "stopped": self.stopped,
            "safety_tripped": self.safety_tripped,
        })


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _moving_rms(values: np.ndarray, window: int) -> np.ndarray:
    window = max(2, min(int(window), values.size))
    squared = np.square(np.asarray(values, dtype=np.float64))
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    valid = np.sqrt(np.maximum(
        (cumulative[window:] - cumulative[:-window]) / window, 0.0
    ))
    left = window // 2
    return np.pad(valid, (left, values.size - valid.size - left), mode="edge")


def _longest_true_run(mask: np.ndarray) -> tuple[int, int]:
    changes = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    if not starts.size:
        raise ValueError("未找到稳定的大信号正弦测量区，请检查ATA监测接线、量程和触发")
    index = int(np.argmax(ends - starts))
    return int(starts[index]), int(ends[index])


def _phasor_fit(
    time_s: np.ndarray, values: np.ndarray, frequency_hz: float
) -> tuple[complex, float, float]:
    omega_t = 2 * np.pi * frequency_hz * time_s
    design = np.column_stack((np.cos(omega_t), np.sin(omega_t), np.ones(time_s.size)))
    coefficients, *_ = np.linalg.lstsq(design, values, rcond=None)
    fitted = design @ coefficients
    centered = values - float(np.mean(values))
    residual = values - fitted
    total_energy = float(np.dot(centered, centered))
    residual_energy = float(np.dot(residual, residual))
    fit_r2 = 1.0 - residual_energy / max(total_energy, 1e-30)
    residual_rms = math.sqrt(residual_energy / max(values.size, 1))
    phasor = complex(float(coefficients[0]), -float(coefficients[1]))
    snr_db = 20 * math.log10(
        max(abs(phasor) / math.sqrt(2), 1e-30) / max(residual_rms, 1e-30)
    )
    return phasor, fit_r2, snr_db


def analyze_big_signal_impedance(
    result: CaptureResult,
    config: BigSignalLcrConfig,
    frequency_hz: float,
) -> dict[str, Any]:
    """Fit monitor phasors and convert them to actual coil impedance."""
    config.validate()
    voltage_monitor = np.asarray(result.volts[config.voltage_channel], dtype=np.float64)
    current_monitor = np.asarray(result.volts[config.current_channel], dtype=np.float64)
    samples_per_cycle = result.actual_sample_rate_hz / frequency_hz
    if samples_per_cycle < 8:
        raise ValueError(
            f"{frequency_hz:g} Hz 时每周期只有 {samples_per_cycle:.2f} 点；"
            "请提高采样率或降低最高频率"
        )
    envelope = _moving_rms(voltage_monitor - np.median(voltage_monitor), round(samples_per_cycle))
    peak_envelope = float(np.max(envelope))
    if peak_envelope <= 1e-9:
        raise ValueError("Voltage Monitor 没有检测到有效的大信号激励")
    start, end = _longest_true_run(envelope >= peak_envelope * 0.55)
    guard = round(config.analysis_guard_cycles * samples_per_cycle)
    start += guard
    end -= guard
    available_cycles = int((end - start) / samples_per_cycle)
    cycles = min(config.analysis_cycles, available_cycles)
    if cycles < 2:
        raise ValueError("稳定大信号正弦区不足 2 个周期，请增加突发周期或减小保护周期")
    count = max(8, round(cycles * samples_per_cycle))
    center = (start + end) // 2
    first = max(start, center - count // 2)
    last = min(end, first + count)
    first = max(start, last - count)
    fit_time = np.asarray(result.time_s[first:last], dtype=np.float64)
    stride = max(1, int(math.ceil(fit_time.size / 200_000)))
    fit_time = fit_time[::stride]
    voltage_phasor, voltage_fit_r2, voltage_snr_db = _phasor_fit(
        fit_time, voltage_monitor[first:last:stride], frequency_hz
    )
    current_phasor, current_fit_r2, current_snr_db = _phasor_fit(
        fit_time, current_monitor[first:last:stride], frequency_hz
    )
    # Offsets do not affect the AC phasor.  They are stored in the config for
    # traceability and are intentionally not subtracted from the sinusoidal fit.
    dut_voltage = voltage_phasor * config.voltage_monitor_scale_v_per_v
    dut_current = config.current_monitor_polarity * current_phasor * config.current_monitor_scale_a_per_v
    if abs(dut_current) <= 1e-15:
        raise ValueError("Current Monitor 幅值过小，无法计算阻抗；请检查换算系数和量程")
    impedance = dut_voltage / dut_current
    resistance = float(impedance.real)
    reactance = float(impedance.imag)
    magnitude = float(abs(impedance))
    phase_deg = float(np.rad2deg(np.angle(impedance)))
    omega = 2 * np.pi * frequency_hz
    quality = abs(reactance) / max(abs(resistance), 1e-30)
    voltage_rms = float(abs(dut_voltage) / math.sqrt(2))
    current_rms = float(abs(dut_current) / math.sqrt(2))
    pretrigger = result.time_s < 0
    voltage_baseline = float(np.median(voltage_monitor[pretrigger])) if np.any(pretrigger) else 0.0
    current_baseline = float(np.median(current_monitor[pretrigger])) if np.any(pretrigger) else 0.0
    voltage_peak = float(np.max(np.abs(voltage_monitor - voltage_baseline)) * config.voltage_monitor_scale_v_per_v)
    current_peak = float(np.max(np.abs(current_monitor - current_baseline)) * config.current_monitor_scale_a_per_v)
    voltage_vpp = float(np.ptp(voltage_monitor) * config.voltage_monitor_scale_v_per_v)
    return {
        "frequency_hz": float(frequency_hz),
        "impedance_real_ohm": resistance,
        "impedance_imag_ohm": reactance,
        "impedance_magnitude_ohm": magnitude,
        "phase_deg": phase_deg,
        "series_resistance_ohm": resistance,
        "series_reactance_ohm": reactance,
        "effective_inductance_h": reactance / omega if reactance > 0 else float("nan"),
        "quality_factor": float(quality),
        "voltage_rms_v": voltage_rms,
        "current_rms_a": current_rms,
        "voltage_peak_v": voltage_peak,
        "voltage_vpp_v": voltage_vpp,
        "current_peak_a": current_peak,
        "voltage_fit_r2": float(voltage_fit_r2),
        "current_fit_r2": float(current_fit_r2),
        "voltage_snr_db": float(voltage_snr_db),
        "current_snr_db": float(current_snr_db),
        "analysis_start_s": float(result.time_s[first]),
        "analysis_end_s": float(result.time_s[last - 1]),
        "analysis_cycles": int(cycles),
    }


def _safety_metrics(
    metrics: dict[str, Any], config: BigSignalLcrConfig, drive_vpp: float
) -> dict[str, Any]:
    voltage_ratio = float(metrics["voltage_vpp_v"]) / config.alert_voltage_vpp_v
    current_ratio = float(metrics["current_peak_a"]) / config.alert_current_peak_a
    drive_ratio = drive_vpp / config.max_drive_vpp
    frequency_ratio = float(metrics["frequency_hz"]) / config.max_frequency_hz
    ratios = {
        "drive": drive_ratio,
        "voltage": voltage_ratio,
        "current": current_ratio,
        "frequency": frequency_ratio,
    }
    stop_reasons: list[str] = []
    warnings: list[str] = []
    if drive_ratio > 1.0:
        stop_reasons.append("AWG输入超过限值")
    if voltage_ratio > 1.0:
        warnings.append(f"线圈电压 {metrics['voltage_vpp_v']:.4g} Vpp 超过 {config.alert_voltage_vpp_v:g} Vpp 提醒值")
    if current_ratio > 1.0:
        warnings.append(f"线圈电流 {metrics['current_peak_a']:.4g} Apeak 超过 {config.alert_current_peak_a:g} Apeak 提醒值")
    if frequency_ratio > 1.0:
        stop_reasons.append("频率超过限值")
    state = "TRIP" if stop_reasons else ("WARN" if warnings or max(ratios.values()) >= 0.8 else "OK")
    messages = stop_reasons + warnings
    return {
        "safety_state": state,
        "safety_message": "；".join(messages) if messages else ("接近程序提醒阈值" if state == "WARN" else "未触及程序提醒阈值"),
        "safety_drive_ratio": float(drive_ratio),
        "safety_voltage_ratio": float(voltage_ratio),
        "safety_current_ratio": float(current_ratio),
        "safety_frequency_ratio": float(frequency_ratio),
    }


def monitor_health(
    result: CaptureResult, config: BigSignalLcrConfig, frequency_hz: float
) -> dict[str, Any]:
    """Infer monitor loss or a sustained mid-burst collapse, never ATA status.

    The check uses complete one-cycle RMS bins within the expected flat burst,
    excluding both programmed ramps. A low signal is a wiring/trigger fault as
    readily as an amplifier trip; only a previously present signal that drops
    during the flat region is labelled *suspected* trip.
    """
    voltage = np.asarray(result.volts[config.voltage_channel], dtype=np.float64)
    voltage_envelope = _moving_rms(voltage - np.median(voltage[: max(2, voltage.size // 20)]),
                                   round(result.actual_sample_rate_hz / frequency_hz))
    peak_envelope = float(np.max(voltage_envelope))
    if peak_envelope < config.min_monitor_rms_v:
        return {"monitor_state": "MONITOR_FAULT", "monitor_message": "Voltage Monitor 未见有效突发信号；检查触发、接线及 ATA 状态"}
    threshold = max(peak_envelope * 0.08, config.min_monitor_rms_v * 0.5)
    active = np.flatnonzero(voltage_envelope > threshold)
    if not active.size or active[0] == 0:
        return {"monitor_state": "MONITOR_FAULT", "monitor_message": "未完整记录突发起点，无法可靠判别疑似跳闸"}
    onset_time = float(result.time_s[int(active[0])])
    first_cycle = math.ceil(config.ramp_cycles + 1)
    # The 8% envelope crossing may lag true AWG onset by up to the full ramp.
    # Exclude that uncertainty from the trailing plateau to avoid mistaking
    # the programmed fade-out for a protection trip.
    last_cycle = math.floor(config.burst_cycles - 2 * config.ramp_cycles - 1)
    cycle_numbers = range(first_cycle, last_cycle)
    if len(cycle_numbers) < 5:
        return {"monitor_state": "OK", "monitor_message": "平顶区过短，未执行掉幅判别"}
    baseline_count = min(5, max(2, len(cycle_numbers) // 3))
    ratios: dict[str, float] = {}
    for label, channel in (("Voltage", config.voltage_channel), ("Current", config.current_channel)):
        signal = np.asarray(result.volts[channel], dtype=np.float64)
        values = []
        for cycle in cycle_numbers:
            first = int(np.searchsorted(result.time_s, onset_time + cycle / frequency_hz))
            last = int(np.searchsorted(result.time_s, onset_time + (cycle + 1) / frequency_hz))
            segment = signal[first:last]
            if segment.size < 8:
                return {"monitor_state": "MONITOR_FAULT", "monitor_message": "采样时间不足以覆盖平顶区"}
            centered = segment - float(np.mean(segment))
            values.append(float(np.sqrt(np.mean(centered * centered))))
        rms = np.asarray(values)
        baseline = float(np.median(rms[:baseline_count]))
        if baseline < config.min_monitor_rms_v:
            return {
                "monitor_state": "MONITOR_FAULT",
                "monitor_message": f"{label} Monitor 平顶区低于 {config.min_monitor_rms_v:g} Vrms；可能未触发、未接线或功放保护",
                "monitor_voltage_baseline_v": baseline if label == "Voltage" else None,
                "monitor_current_baseline_v": baseline if label == "Current" else None,
            }
        after = rms[baseline_count:]
        ratio = after / baseline
        ratios[label.lower()] = float(np.min(ratio)) if ratio.size else 1.0
        hold = math.ceil(config.trip_hold_cycles)
        if config.trip_detection_enabled and ratio.size >= hold:
            collapsed = np.convolve((ratio < config.trip_drop_ratio).astype(int), np.ones(hold, dtype=int), "valid")
            if np.any(collapsed == hold):
                return {
                    "monitor_state": "SUSPECT_TRIP",
                    "monitor_message": f"{label} Monitor 平顶区持续掉幅；仅为疑似跳闸，请检查 ATA 面板",
                    "monitor_min_ratio": float(np.min(ratio)),
                }
    return {
        "monitor_state": "OK", "monitor_message": "双监测通道平顶区有效",
        "monitor_min_ratio": float(min(ratios.values())),
    }


def _suggest_monitor_ranges(
    result: CaptureResult, acquisition: AcquisitionConfig, config: BigSignalLcrConfig
) -> tuple[dict[str, str], str | None]:
    """Choose Pico ADC ranges from measured peaks, retaining trigger headroom."""
    names = list(RANGE_VOLTS)
    changes: dict[str, str] = {}
    for channel in (config.voltage_channel, config.current_channel):
        current = acquisition.channels[channel].range
        index = names.index(current)
        full_scale = RANGE_VOLTS[current]
        peak = float(np.max(np.abs(result.volts[channel])))
        overflow = channel in result.overflow_channels
        if overflow or peak >= full_scale * 0.88:
            if index == len(names) - 1:
                return {}, f"{channel} 通道在 ±50 V 最大量程仍溢出/接近满量程"
            target = next(
                (name for name in names[index + 1:] if RANGE_VOLTS[name] >= peak / 0.65),
                names[-1],
            )
            changes[channel] = target
        elif peak < full_scale * 0.18 and peak > 0:
            threshold = abs(acquisition.trigger.threshold_v) if acquisition.trigger.source == channel else 0.0
            needed = max(peak / 0.65, threshold * 1.25)
            target = next((name for name in names if RANGE_VOLTS[name] >= needed), names[-1])
            if RANGE_VOLTS[target] < full_scale:
                changes[channel] = target
    return changes, None


def _capture_peak_metrics(result: CaptureResult, config: BigSignalLcrConfig) -> dict[str, float]:
    """Measure the complete burst for software alerts, including range retries."""
    pretrigger = result.time_s < 0
    peaks: dict[str, float] = {}
    for label, channel, scale in (
        ("voltage", config.voltage_channel, config.voltage_monitor_scale_v_per_v),
        ("current", config.current_channel, config.current_monitor_scale_a_per_v),
    ):
        signal = np.asarray(result.volts[channel], dtype=np.float64)
        baseline = float(np.median(signal[pretrigger])) if np.any(pretrigger) else 0.0
        peaks[label] = float(np.max(np.abs(signal - baseline)) * scale)
    voltage_signal = np.asarray(result.volts[config.voltage_channel], dtype=np.float64)
    return {
        "voltage_peak_v": peaks["voltage"],
        "current_peak_a": peaks["current"],
        "voltage_vpp_v": float(np.ptp(voltage_signal) * config.voltage_monitor_scale_v_per_v),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def aggregate_big_signal_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[float, float], list[dict[str, Any]]] = {}
    for row in rows:
        key = (float(row["frequency_hz"]), float(row.get("awg_drive_vpp", 0.0)))
        groups.setdefault(key, []).append(row)
    excluded = {"run_index", "repeat", "npz_file", "auto_range_attempts"}
    result: list[dict[str, Any]] = []
    for (frequency, drive_vpp), group in sorted(groups.items()):
        valid = [item for item in group if item.get("safety_state") in {"OK", "WARN"}]
        severity = ("SUSPECT_TRIP", "MONITOR_FAULT", "ADC_OVERFLOW", "ANALYSIS_ERROR", "TRIP", "WARN")
        state = next((item for item in severity if any(row.get("safety_state") == item for row in group)), "OK")
        summary: dict[str, Any] = {
            "frequency_hz": frequency,
            "awg_drive_vpp": drive_vpp,
            "repeats_completed": len(group),
            "valid_repeats": len(valid),
            "safety_state": state,
            "safety_message": next((row.get("safety_message", "") for row in group if row.get("safety_state") == state), ""),
        }
        if not valid:
            result.append(summary)
            continue
        for key, value in valid[0].items():
            if key in excluded or key in {"safety_state", "safety_message"} or not isinstance(value, (int, float)):
                continue
            values = np.asarray([float(item[key]) for item in valid if key in item], dtype=np.float64)
            finite = values[np.isfinite(values)]
            summary[key] = float(np.mean(finite)) if finite.size else float("nan")
            summary[f"{key}_std"] = float(np.std(finite, ddof=1)) if finite.size > 1 else 0.0
        result.append(summary)
    return result


BigSignalProgress = Callable[[dict[str, Any], CaptureResult | None], None]
BigSignalTripHandler = Callable[[dict[str, Any]], bool]


def execute_big_signal_lcr(
    base_config: AcquisitionConfig,
    config: BigSignalLcrConfig,
    device: Pico4824A,
    output_root: Path,
    stop_event: threading.Event,
    progress: BigSignalProgress | None = None,
    on_trip: BigSignalTripHandler | None = None,
) -> BigSignalLcrOutcome:
    base_config.validate()
    config.validate()
    for name in (config.voltage_channel, config.current_channel):
        if not base_config.channels[name].enabled:
            raise ValueError(f"大信号 LCR 监测通道 {name} 尚未启用")
    if base_config.sample_rate_hz / max(config.frequencies_hz) < 8:
        raise ValueError("最高大信号 LCR 频率至少需要每周期 8 个采样点")

    stamp = datetime.now().strftime("big_lcr_%Y%m%d_%H%M%S_%f")
    directory = output_root / stamp
    raw_dir = directory / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    (directory / "big_lcr_config.json").write_text(
        json_dumps({"base_config": base_config.to_dict(), "big_lcr": asdict(config)}),
        encoding="utf-8",
    )

    rows: list[dict[str, Any]] = []
    points = config.parameter_points
    total_runs = len(points) * config.repeats
    run_index = 0
    safety_tripped = False
    current_ranges = {
        name: base_config.channels[name].range
        for name in (config.voltage_channel, config.current_channel)
    }
    for point_index, (frequency, drive_vpp) in enumerate(points, start=1):
        for repeat in range(1, config.repeats + 1):
            if stop_event.is_set():
                summary = aggregate_big_signal_rows(rows)
                _write_csv(directory / "runs.csv", rows)
                _write_csv(directory / "summary.csv", summary)
                return BigSignalLcrOutcome(directory, rows, summary, True, safety_tripped)
            run_index += 1
            capture_config = AcquisitionConfig.from_dict(base_config.to_dict())
            capture_config.awg.enabled = True
            capture_config.awg.waveform = "lcr_tone"
            capture_config.awg.frequency_hz = frequency
            capture_config.awg.cycles = config.burst_cycles
            capture_config.awg.pk_to_pk_v = drive_vpp
            capture_config.awg.offset_v = 0.0
            capture_config.awg.trigger_source = "software"
            capture_config.awg.tone_ramp_cycles = config.ramp_cycles
            pre_duration = max((config.ramp_cycles + 3.0) / frequency, 50e-6)
            post_duration = (config.burst_cycles + 5.0) / frequency
            capture_config.pre_trigger_samples = max(1, round(capture_config.sample_rate_hz * pre_duration))
            capture_config.post_trigger_samples = max(2, round(capture_config.sample_rate_hz * post_duration))
            capture_config.trigger.enabled = True
            capture_config.trigger.source = config.trigger_source_channel
            for name, range_name in current_ranges.items():
                capture_config.channels[name].range = range_name
            if config.auto_monitor_range:
                # Panel gain is an estimate, never a substitute for measured
                # monitor voltage. It only helps avoid a clipped first shot.
                predicted_monitor_peak = (
                    drive_vpp * config.ata_voltage_gain
                    / (2 * config.voltage_monitor_scale_v_per_v)
                )
                voltage_range = capture_config.channels[config.voltage_channel].range
                if predicted_monitor_peak > 0.65 * RANGE_VOLTS[voltage_range]:
                    capture_config.channels[config.voltage_channel].range = next(
                        (name for name, limit in RANGE_VOLTS.items()
                         if limit >= predicted_monitor_peak / 0.65), "50V"
                    )
            capture_config.validate()
            capture = None
            path = None
            range_error = None
            observed_peaks = {"voltage_peak_v": 0.0, "voltage_vpp_v": 0.0, "current_peak_a": 0.0}
            early_health: dict[str, Any] | None = None
            attempts = 0
            for attempt in range(config.auto_range_max_retries + 1):
                if stop_event.is_set():
                    summary = aggregate_big_signal_rows(rows)
                    _write_csv(directory / "runs.csv", rows)
                    _write_csv(directory / "summary.csv", summary)
                    return BigSignalLcrOutcome(directory, rows, summary, True, safety_tripped)
                attempts = attempt + 1
                if progress:
                    progress({
                        "phase": "auto_range" if attempt else "capturing",
                        "run_index": run_index, "total_runs": total_runs,
                        "point_index": point_index, "total_points": len(points),
                        "repeat": repeat, "repeats": config.repeats, "frequency_hz": frequency,
                        "awg_drive_vpp": drive_vpp, "range_attempt": attempts,
                    }, None)
                try:
                    capture = device.capture(capture_config)
                except Exception:
                    if stop_event.is_set():
                        summary = aggregate_big_signal_rows(rows)
                        _write_csv(directory / "runs.csv", rows)
                        _write_csv(directory / "summary.csv", summary)
                        return BigSignalLcrOutcome(directory, rows, summary, True, safety_tripped)
                    raise
                path = save_npz(
                    capture,
                    raw_dir / (
                        f"p{point_index:05d}_f{frequency:012.3f}_v{drive_vpp:07.4f}"
                        f"_r{repeat:03d}_a{attempts:02d}.npz"
                    ),
                )
                for key, value in _capture_peak_metrics(capture, config).items():
                    observed_peaks[key] = max(observed_peaks[key], value)
                if not capture.overflow_channels:
                    early_health = monitor_health(capture, config, frequency)
                    if early_health["monitor_state"] != "OK":
                        break
                if not config.auto_monitor_range:
                    break
                changes, range_error = _suggest_monitor_ranges(capture, capture_config, config)
                if range_error or not changes:
                    break
                if attempt == config.auto_range_max_retries:
                    range_error = "自动量程重采次数用尽，尚未达到合适量程"
                    break
                for name, next_range in changes.items():
                    capture_config.channels[name].range = next_range
                capture_config.validate()
            assert capture is not None and path is not None
            current_ranges = {
                name: capture.config.channels[name].range
                for name in (config.voltage_channel, config.current_channel)
            }
            if capture.overflow_channels or range_error:
                metrics = {
                    "frequency_hz": float(frequency),
                    "safety_state": "ADC_OVERFLOW",
                    "safety_message": range_error or f"ADC 溢出：{','.join(capture.overflow_channels)}",
                }
            else:
                health = early_health or monitor_health(capture, config, frequency)
                if health["monitor_state"] != "OK":
                    metrics = {
                        "frequency_hz": float(frequency),
                        "safety_state": health["monitor_state"],
                        "safety_message": health["monitor_message"],
                        **health,
                    }
                else:
                    try:
                        metrics = analyze_big_signal_impedance(capture, config, frequency)
                        metrics.update(observed_peaks)
                        metrics.update(_safety_metrics(metrics, config, drive_vpp))
                        metrics.update(health)
                    except ValueError as exc:
                        metrics = {
                            "frequency_hz": float(frequency),
                            "safety_state": "ANALYSIS_ERROR",
                            "safety_message": str(exc),
                        }
            row = {
                "run_index": run_index, "repeat": repeat, "awg_drive_vpp": drive_vpp,
                "npz_file": str(path.relative_to(directory)), **metrics,
                "auto_range_attempts": attempts,
                "voltage_range": capture.config.channels[config.voltage_channel].range,
                "current_range": capture.config.channels[config.current_channel].range,
                "voltage_monitor_channel": config.voltage_channel,
                "current_monitor_channel": config.current_channel,
                "voltage_monitor_scale_v_per_v": config.voltage_monitor_scale_v_per_v,
                "current_monitor_scale_a_per_v": config.current_monitor_scale_a_per_v,
                "current_monitor_polarity": config.current_monitor_polarity,
                "ata_voltage_gain": config.ata_voltage_gain,
            }
            rows.append(row)
            summary = aggregate_big_signal_rows(rows)
            _write_csv(directory / "runs.csv", rows)
            _write_csv(directory / "summary.csv", summary)
            if progress:
                progress({
                    "phase": "saved", "run_index": run_index, "total_runs": total_runs,
                    "point_index": point_index, "total_points": len(points),
                    "repeat": repeat, "repeats": config.repeats, "frequency_hz": frequency,
                    "awg_drive_vpp": drive_vpp,
                    "safety_state": metrics["safety_state"],
                }, capture)
            if metrics["safety_state"] not in {"OK", "WARN"}:
                safety_tripped = True
                if metrics["safety_state"] != "SUSPECT_TRIP":
                    return BigSignalLcrOutcome(directory, rows, summary, True, safety_tripped=True)
                # Without an operator-controlled pause handler, fail closed as
                # before. The Web UI waits here before any next excitation.
                if on_trip is None or not on_trip(row) or stop_event.is_set():
                    return BigSignalLcrOutcome(directory, rows, summary, True, safety_tripped=True)
            if run_index < total_runs and stop_event.wait(config.interval_s):
                return BigSignalLcrOutcome(directory, rows, summary, True, safety_tripped)
    summary = aggregate_big_signal_rows(rows)
    return BigSignalLcrOutcome(directory, rows, summary, False, safety_tripped)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
