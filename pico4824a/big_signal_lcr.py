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

from .config import AcquisitionConfig, CHANNEL_NAMES
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

    mode: str = "single"
    frequency_hz: float = 100_000.0
    frequency_start_hz: float = 20_000.0
    frequency_stop_hz: float = 200_000.0
    frequency_step_hz: float = 5_000.0
    points_per_decade: int = 12
    repeats: int = 1
    interval_s: float = 0.2
    awg_drive_vpp: float = 0.5
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
    max_drive_vpp: float = 2.0
    max_voltage_rms_v: float = 20.0
    max_current_rms_a: float = 1.0
    max_frequency_hz: float = 1_000_000.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BigSignalLcrConfig":
        config = cls(**raw)
        config.validate()
        return config

    def validate(self) -> None:
        self.mode = str(self.mode).lower()
        if self.mode not in {"single", "linear", "log"}:
            raise ValueError("大信号 LCR 模式必须是 single、linear 或 log")
        if not 1.0 <= self.frequency_hz <= 1_000_000.0:
            raise ValueError("大信号 LCR 单点频率必须在 1 Hz 到 1 MHz 之间")
        if not 1.0 <= self.frequency_start_hz <= self.frequency_stop_hz <= 1_000_000.0:
            raise ValueError("大信号 LCR 扫频范围必须在 1 Hz 到 1 MHz 之间")
        if self.frequency_step_hz <= 0:
            raise ValueError("大信号 LCR 线性扫频步长必须大于 0")
        if not 1 <= self.points_per_decade <= 200:
            raise ValueError("大信号 LCR 每十倍频程点数必须在 1 到 200 之间")
        if not 1 <= self.repeats <= 100:
            raise ValueError("大信号 LCR 每点重复次数必须在 1 到 100 之间")
        if not 0 <= self.interval_s <= 3600:
            raise ValueError("大信号 LCR 测量间隔必须在 0 到 3600 秒之间")
        if not 0 < self.awg_drive_vpp <= 4.0:
            raise ValueError("ATA 激励的 Pico AWG 输入必须在 0 到 4 Vpp 之间")
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
            ("最大线圈电压", self.max_voltage_rms_v),
            ("最大线圈电流", self.max_current_rms_a),
            ("最大测量频率", self.max_frequency_hz),
        ):
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{label}必须是大于0的有限数")
        if self.awg_drive_vpp > self.max_drive_vpp:
            raise ValueError("ATA 激励超过设定的最大 AWG 输入安全限值")
        if self.max_frequency_hz > 1_000_000.0:
            raise ValueError("最大测量频率不能超过 1 MHz")
        if max(self.frequencies_hz) > self.max_frequency_hz:
            raise ValueError("测量频率超过 ATA 安全状态中的最大频率")
        if len(self.frequencies_hz) * self.repeats > 10_000:
            raise ValueError("一次大信号 LCR 扫描最多允许 10000 次采集")

    @property
    def frequencies_hz(self) -> tuple[float, ...]:
        if self.mode == "single":
            return (float(self.frequency_hz),)
        if self.mode == "linear":
            count = int(math.floor(
                (self.frequency_stop_hz - self.frequency_start_hz)
                / self.frequency_step_hz
                + 1e-12
            )) + 1
            values = [
                self.frequency_start_hz + index * self.frequency_step_hz
                for index in range(count)
            ]
            if values[-1] < self.frequency_stop_hz * (1 - 1e-12):
                values.append(self.frequency_stop_hz)
            return tuple(float(min(value, self.frequency_stop_hz)) for value in values)
        decades = math.log10(self.frequency_stop_hz / self.frequency_start_hz)
        count = max(2, int(math.ceil(decades * self.points_per_decade)) + 1)
        return tuple(float(value) for value in np.geomspace(
            self.frequency_start_hz, self.frequency_stop_hz, count
        ))

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
        "voltage_fit_r2": float(voltage_fit_r2),
        "current_fit_r2": float(current_fit_r2),
        "voltage_snr_db": float(voltage_snr_db),
        "current_snr_db": float(current_snr_db),
        "analysis_start_s": float(result.time_s[first]),
        "analysis_end_s": float(result.time_s[last - 1]),
        "analysis_cycles": int(cycles),
    }


def _safety_metrics(
    metrics: dict[str, Any], config: BigSignalLcrConfig
) -> dict[str, Any]:
    voltage_ratio = float(metrics["voltage_rms_v"]) / config.max_voltage_rms_v
    current_ratio = float(metrics["current_rms_a"]) / config.max_current_rms_a
    drive_ratio = config.awg_drive_vpp / config.max_drive_vpp
    frequency_ratio = float(metrics["frequency_hz"]) / config.max_frequency_hz
    ratios = {
        "drive": drive_ratio,
        "voltage": voltage_ratio,
        "current": current_ratio,
        "frequency": frequency_ratio,
    }
    reasons: list[str] = []
    if drive_ratio > 1.0:
        reasons.append("AWG输入超过限值")
    if voltage_ratio > 1.0:
        reasons.append("线圈电压超过限值")
    if current_ratio > 1.0:
        reasons.append("线圈电流超过限值")
    if frequency_ratio > 1.0:
        reasons.append("频率超过限值")
    state = "TRIP" if reasons else ("WARN" if max(ratios.values()) >= 0.8 else "OK")
    return {
        "safety_state": state,
        "safety_message": "；".join(reasons) if reasons else ("接近安全限值" if state == "WARN" else "在安全限值内"),
        "safety_drive_ratio": float(drive_ratio),
        "safety_voltage_ratio": float(voltage_ratio),
        "safety_current_ratio": float(current_ratio),
        "safety_frequency_ratio": float(frequency_ratio),
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
    groups: dict[float, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(float(row["frequency_hz"]), []).append(row)
    excluded = {"run_index", "repeat", "npz_file"}
    result: list[dict[str, Any]] = []
    for frequency, group in sorted(groups.items()):
        summary: dict[str, Any] = {
            "frequency_hz": frequency,
            "repeats_completed": len(group),
            "safety_state": "TRIP" if any(item.get("safety_state") == "TRIP" for item in group)
            else ("WARN" if any(item.get("safety_state") == "WARN" for item in group) else "OK"),
        }
        for key, value in group[0].items():
            if key in excluded or key in {"safety_state", "safety_message"} or not isinstance(value, (int, float)):
                continue
            values = np.asarray([float(item[key]) for item in group], dtype=np.float64)
            finite = values[np.isfinite(values)]
            summary[key] = float(np.mean(finite)) if finite.size else float("nan")
            summary[f"{key}_std"] = float(np.std(finite, ddof=1)) if finite.size > 1 else 0.0
        result.append(summary)
    return result


BigSignalProgress = Callable[[dict[str, Any], CaptureResult | None], None]


def execute_big_signal_lcr(
    base_config: AcquisitionConfig,
    config: BigSignalLcrConfig,
    device: Pico4824A,
    output_root: Path,
    stop_event: threading.Event,
    progress: BigSignalProgress | None = None,
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
    total_runs = len(config.frequencies_hz) * config.repeats
    run_index = 0
    for point_index, frequency in enumerate(config.frequencies_hz, start=1):
        for repeat in range(1, config.repeats + 1):
            if stop_event.is_set():
                summary = aggregate_big_signal_rows(rows)
                _write_csv(directory / "runs.csv", rows)
                _write_csv(directory / "summary.csv", summary)
                return BigSignalLcrOutcome(directory, rows, summary, True)
            run_index += 1
            capture_config = AcquisitionConfig.from_dict(base_config.to_dict())
            capture_config.awg.enabled = True
            capture_config.awg.waveform = "lcr_tone"
            capture_config.awg.frequency_hz = frequency
            capture_config.awg.cycles = config.burst_cycles
            capture_config.awg.pk_to_pk_v = config.awg_drive_vpp
            capture_config.awg.offset_v = 0.0
            capture_config.awg.trigger_source = "software"
            capture_config.awg.tone_ramp_cycles = config.ramp_cycles
            pre_duration = max(2.0 / frequency, 50e-6)
            post_duration = (config.burst_cycles + 5.0) / frequency
            capture_config.pre_trigger_samples = max(1, round(capture_config.sample_rate_hz * pre_duration))
            capture_config.post_trigger_samples = max(2, round(capture_config.sample_rate_hz * post_duration))
            capture_config.trigger.enabled = True
            capture_config.trigger.source = config.trigger_source_channel
            capture_config.validate()
            if progress:
                progress({
                    "phase": "capturing", "run_index": run_index, "total_runs": total_runs,
                    "point_index": point_index, "total_points": len(config.frequencies_hz),
                    "repeat": repeat, "repeats": config.repeats, "frequency_hz": frequency,
                }, None)
            try:
                capture = device.capture(capture_config)
            except Exception:
                if stop_event.is_set():
                    summary = aggregate_big_signal_rows(rows)
                    return BigSignalLcrOutcome(directory, rows, summary, True)
                raise
            path = save_npz(capture, raw_dir / f"f{frequency:012.3f}_r{repeat:03d}.npz")
            metrics = analyze_big_signal_impedance(capture, config, frequency)
            metrics.update(_safety_metrics(metrics, config))
            row = {
                "run_index": run_index, "repeat": repeat,
                "npz_file": str(path.relative_to(directory)), **metrics,
                "voltage_monitor_channel": config.voltage_channel,
                "current_monitor_channel": config.current_channel,
                "voltage_monitor_scale_v_per_v": config.voltage_monitor_scale_v_per_v,
                "current_monitor_scale_a_per_v": config.current_monitor_scale_a_per_v,
                "current_monitor_polarity": config.current_monitor_polarity,
            }
            rows.append(row)
            summary = aggregate_big_signal_rows(rows)
            _write_csv(directory / "runs.csv", rows)
            _write_csv(directory / "summary.csv", summary)
            if progress:
                progress({
                    "phase": "saved", "run_index": run_index, "total_runs": total_runs,
                    "point_index": point_index, "total_points": len(config.frequencies_hz),
                    "repeat": repeat, "repeats": config.repeats, "frequency_hz": frequency,
                    "safety_state": metrics["safety_state"],
                }, capture)
            if metrics["safety_state"] == "TRIP":
                return BigSignalLcrOutcome(directory, rows, summary, True, safety_tripped=True)
            if run_index < total_runs and stop_event.wait(config.interval_s):
                return BigSignalLcrOutcome(directory, rows, summary, True)
    summary = aggregate_big_signal_rows(rows)
    return BigSignalLcrOutcome(directory, rows, summary, False)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
