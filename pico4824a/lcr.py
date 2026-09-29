"""LCR/complex-impedance measurement using Pico AWG and two ADC channels."""

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
class LcrConfig:
    mode: str = "single"
    frequency_hz: float = 100_000.0
    frequency_start_hz: float = 20_000.0
    frequency_stop_hz: float = 200_000.0
    frequency_step_hz: float = 5_000.0
    points_per_decade: int = 12
    repeats: int = 1
    interval_s: float = 0.2
    excitation_vpp: float = 1.0
    burst_cycles: int = 40
    ramp_cycles: float = 3.0
    analysis_cycles: int = 16
    analysis_guard_cycles: float = 2.0
    voltage_channel: str = "A"
    current_channel: str = "B"
    trigger_signal: str = "voltage"
    feedback_resistance_ohm: float = 1_000.0
    voltage_gain: float = 1.9933774834
    current_gain: float = 1.9933774834
    current_polarity: int = -1
    impedance_scale: float = 1.0
    phase_correction_deg: float = 0.0
    series_resistance_ohm: float = 0.0
    series_reactance_ohm: float = 0.0
    parallel_conductance_s: float = 0.0
    parallel_susceptance_s: float = 0.0
    calibration_enabled: bool = False
    calibration_file: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LcrConfig":
        config = cls(**raw)
        config.validate()
        return config

    def validate(self) -> None:
        self.mode = str(self.mode).lower()
        if self.mode not in {"single", "linear", "log"}:
            raise ValueError("LCR 模式必须是 single、linear 或 log")
        if not 1.0 <= self.frequency_hz <= 1_000_000.0:
            raise ValueError("LCR 单点频率必须在 1 Hz 到 1 MHz 之间")
        if not 1.0 <= self.frequency_start_hz <= self.frequency_stop_hz <= 1_000_000.0:
            raise ValueError("LCR 扫频范围必须在 1 Hz 到 1 MHz 之间")
        if self.frequency_step_hz <= 0:
            raise ValueError("LCR 线性扫频步长必须大于 0")
        if not 1 <= self.points_per_decade <= 200:
            raise ValueError("每十倍频程点数必须在 1 到 200 之间")
        if not 1 <= self.repeats <= 100:
            raise ValueError("LCR 每点重复次数必须在 1 到 100 之间")
        if not 0 <= self.interval_s <= 3600:
            raise ValueError("LCR 测量间隔必须在 0 到 3600 秒之间")
        if not 0 < self.excitation_vpp <= 4.0:
            raise ValueError("LCR 激励必须在 0 到 4 Vpp 之间")
        if not 8 <= self.burst_cycles <= 10_000:
            raise ValueError("LCR 突发周期数必须在 8 到 10000 之间")
        if self.ramp_cycles < 0 or self.ramp_cycles * 2 + 2 >= self.burst_cycles:
            raise ValueError("LCR 上下沿周期过长，必须保留至少 2 个平顶周期")
        if not 2 <= self.analysis_cycles < self.burst_cycles - 2 * self.ramp_cycles:
            raise ValueError("LCR 分析周期数必须位于平顶区内")
        if self.analysis_guard_cycles < 0:
            raise ValueError("LCR 分析保护周期不能为负")
        names = {
            "电压": str(self.voltage_channel).upper(),
            "电流": str(self.current_channel).upper(),
        }
        if any(value not in CHANNEL_NAMES for value in names.values()):
            raise ValueError("LCR 电压和电流通道必须从 A 到 H 中选择")
        if len(set(names.values())) != 2:
            raise ValueError("LCR 电压和电流必须使用两个不同通道")
        self.voltage_channel = names["电压"]
        self.current_channel = names["电流"]
        self.trigger_signal = str(self.trigger_signal).lower()
        if self.trigger_signal not in {"voltage", "current"}:
            raise ValueError("LCR 触发信号必须是 voltage 或 current")
        if self.feedback_resistance_ohm <= 0:
            raise ValueError("反馈电阻 Rf 必须大于 0")
        if self.voltage_gain <= 0 or self.current_gain <= 0:
            raise ValueError("两路模拟前端增益必须大于 0")
        if self.current_polarity not in {-1, 1}:
            raise ValueError("电流极性必须是 -1 或 +1")
        if self.impedance_scale <= 0:
            raise ValueError("阻抗幅值校正必须大于 0")
        if self.calibration_enabled and not self.calibration_file:
            raise ValueError("启用精准电阻校准时必须选择校准文件")
        if len(self.frequencies_hz) * self.repeats > 10_000:
            raise ValueError("一次 LCR 扫描最多允许 10000 次采集")

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
        return tuple(
            float(value)
            for value in np.geomspace(
                self.frequency_start_hz, self.frequency_stop_hz, count
            )
        )

    @property
    def trigger_source_channel(self) -> str:
        """Reuse one measured output as trigger; never require a third cable."""
        return (
            self.voltage_channel
            if self.trigger_signal == "voltage"
            else self.current_channel
        )


@dataclass(slots=True)
class LcrOutcome:
    directory: Path
    run_rows: list[dict[str, Any]]
    summary_rows: list[dict[str, Any]]
    stopped: bool
    calibration_file: Path | None = None
    calibration_standard_ohm: float | None = None

    def payload(self) -> dict[str, Any]:
        return _json_safe({
            "directory": str(self.directory),
            "run_rows": self.run_rows,
            "summary_rows": self.summary_rows,
            "stopped": self.stopped,
            "calibration_file": (
                str(self.calibration_file) if self.calibration_file is not None else None
            ),
            "calibration_standard_ohm": self.calibration_standard_ohm,
        })


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclass(slots=True)
class LcrCalibration:
    """Frequency-dependent complex correction produced by a precision resistor."""

    path: Path
    standard_resistance_ohm: float
    signature: dict[str, Any]
    frequencies_hz: np.ndarray
    factors: np.ndarray

    def factor_at(self, frequency_hz: float) -> complex:
        frequencies = self.frequencies_hz
        if frequencies.size == 0:
            raise ValueError("校准文件没有有效频点")
        exact = np.flatnonzero(
            np.isclose(frequencies, frequency_hz, rtol=1e-9, atol=1e-6)
        )
        if exact.size:
            return complex(self.factors[int(exact[0])])
        if frequencies.size == 1:
            raise ValueError(
                f"校准文件只有 {frequencies[0]:g} Hz，不能用于 {frequency_hz:g} Hz"
            )
        if frequency_hz < frequencies[0] or frequency_hz > frequencies[-1]:
            raise ValueError(
                f"频率 {frequency_hz:g} Hz 超出校准范围 "
                f"{frequencies[0]:g}–{frequencies[-1]:g} Hz"
            )
        log_frequency = np.log(frequencies)
        target = math.log(frequency_hz)
        real = np.interp(target, log_frequency, self.factors.real)
        imag = np.interp(target, log_frequency, self.factors.imag)
        return complex(float(real), float(imag))


def _calibration_signature(
    base_config: AcquisitionConfig, lcr: LcrConfig
) -> dict[str, Any]:
    """Settings that must remain unchanged for a resistor calibration to be valid."""
    return {
        "sample_rate_hz": float(base_config.sample_rate_hz),
        "voltage_channel": lcr.voltage_channel,
        "current_channel": lcr.current_channel,
        "voltage_range": base_config.channels[lcr.voltage_channel].range,
        "current_range": base_config.channels[lcr.current_channel].range,
        "voltage_coupling": base_config.channels[lcr.voltage_channel].coupling.upper(),
        "current_coupling": base_config.channels[lcr.current_channel].coupling.upper(),
        "feedback_resistance_ohm": float(lcr.feedback_resistance_ohm),
        "voltage_gain": float(lcr.voltage_gain),
        "current_gain": float(lcr.current_gain),
        "current_polarity": int(lcr.current_polarity),
        "impedance_scale": float(lcr.impedance_scale),
        "phase_correction_deg": float(lcr.phase_correction_deg),
        "excitation_vpp": float(lcr.excitation_vpp),
    }


def _signature_mismatches(
    expected: dict[str, Any], actual: dict[str, Any]
) -> list[str]:
    mismatches: list[str] = []
    for key, expected_value in expected.items():
        if key not in actual:
            mismatches.append(key)
            continue
        actual_value = actual[key]
        if isinstance(expected_value, (int, float)) and not isinstance(expected_value, bool):
            try:
                matches = math.isclose(
                    float(expected_value), float(actual_value), rel_tol=1e-9, abs_tol=1e-12
                )
            except (TypeError, ValueError):
                matches = False
        else:
            matches = expected_value == actual_value
        if not matches:
            mismatches.append(key)
    return mismatches


def load_lcr_calibration(
    path: str | Path, base_config: AcquisitionConfig, lcr: LcrConfig
) -> LcrCalibration:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"LCR 校准文件不存在：{source}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 LCR 校准文件：{exc}") from exc
    if raw.get("schema") != "pico4824a-lcr-resistor-calibration-v1":
        raise ValueError("不支持的 LCR 校准文件格式")
    standard = float(raw.get("standard_resistance_ohm", 0.0))
    if not math.isfinite(standard) or standard <= 0:
        raise ValueError("校准文件中的标准电阻值无效")
    signature = raw.get("signature")
    if not isinstance(signature, dict):
        raise ValueError("校准文件缺少测量配置签名")
    expected = _calibration_signature(base_config, lcr)
    mismatches = _signature_mismatches(expected, signature)
    if mismatches:
        raise ValueError(
            "当前LCR设置与校准时不一致：" + "、".join(mismatches)
            + "；请恢复原设置或重新校准"
        )
    points = raw.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError("校准文件没有频点数据")
    parsed: list[tuple[float, complex]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        frequency = float(point.get("frequency_hz", 0.0))
        factor = complex(
            float(point.get("factor_real", float("nan"))),
            float(point.get("factor_imag", float("nan"))),
        )
        if frequency > 0 and math.isfinite(frequency) and math.isfinite(factor.real) and math.isfinite(factor.imag):
            parsed.append((frequency, factor))
    if not parsed:
        raise ValueError("校准文件没有有效的复数校准系数")
    parsed.sort(key=lambda item: item[0])
    return LcrCalibration(
        path=source,
        standard_resistance_ohm=standard,
        signature=signature,
        frequencies_hz=np.asarray([item[0] for item in parsed], dtype=np.float64),
        factors=np.asarray([item[1] for item in parsed], dtype=np.complex128),
    )


def _moving_rms(values: np.ndarray, window: int) -> np.ndarray:
    window = max(2, min(int(window), values.size))
    squared = np.square(np.asarray(values, dtype=np.float64))
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    valid = np.sqrt(np.maximum(
        (cumulative[window:] - cumulative[:-window]) / window,
        0.0,
    ))
    left = window // 2
    return np.pad(valid, (left, values.size - valid.size - left), mode="edge")


def _longest_true_run(mask: np.ndarray) -> tuple[int, int]:
    changes = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    if not starts.size:
        raise ValueError("未找到稳定的 LCR 正弦测量区，请检查接线、量程和触发")
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
    snr_db = 20 * math.log10(max(abs(phasor) / math.sqrt(2), 1e-30) / max(residual_rms, 1e-30))
    return phasor, fit_r2, snr_db


def analyze_impedance(
    result: CaptureResult,
    config: LcrConfig,
    frequency_hz: float,
    calibration_factor: complex = 1.0 + 0.0j,
) -> dict[str, Any]:
    """Estimate complex impedance from voltage/current front-end outputs."""
    config.validate()
    voltage = np.asarray(result.volts[config.voltage_channel], dtype=np.float64)
    current_output = np.asarray(result.volts[config.current_channel], dtype=np.float64)
    samples_per_cycle = result.actual_sample_rate_hz / frequency_hz
    if samples_per_cycle < 8:
        raise ValueError(
            f"{frequency_hz:g} Hz 时每周期只有 {samples_per_cycle:.2f} 点；"
            "请提高采样率或降低最高频率"
        )
    envelope = _moving_rms(voltage - np.median(voltage), round(samples_per_cycle))
    peak_envelope = float(np.max(envelope))
    if peak_envelope <= 1e-9:
        raise ValueError("电压通道没有检测到有效 LCR 激励")
    start, end = _longest_true_run(envelope >= peak_envelope * 0.55)
    guard = round(config.analysis_guard_cycles * samples_per_cycle)
    start += guard
    end -= guard
    available_cycles = int((end - start) / samples_per_cycle)
    cycles = min(config.analysis_cycles, available_cycles)
    if cycles < 2:
        raise ValueError("稳定正弦区不足 2 个周期，请增加突发周期或减小保护周期")
    count = max(8, round(cycles * samples_per_cycle))
    center = (start + end) // 2
    first = max(start, center - count // 2)
    last = min(end, first + count)
    first = max(start, last - count)
    fit_time = np.asarray(result.time_s[first:last], dtype=np.float64)
    # Keep least-squares memory bounded without sacrificing coherent phase.
    stride = max(1, int(math.ceil(fit_time.size / 200_000)))
    fit_time = fit_time[::stride]
    voltage_phasor, voltage_fit_r2, voltage_snr_db = _phasor_fit(
        fit_time, voltage[first:last:stride], frequency_hz
    )
    current_output_phasor, current_fit_r2, current_snr_db = _phasor_fit(
        fit_time, current_output[first:last:stride], frequency_hz
    )
    dut_voltage = voltage_phasor / config.voltage_gain
    dut_current = (
        config.current_polarity
        * current_output_phasor
        / (config.current_gain * config.feedback_resistance_ohm)
    )
    if abs(dut_current) <= 1e-15:
        raise ValueError("电流通道幅值过小，无法计算阻抗；请调整反馈电阻或输入量程")
    impedance = dut_voltage / dut_current
    impedance *= config.impedance_scale * np.exp(
        1j * np.deg2rad(config.phase_correction_deg)
    )
    uncalibrated_impedance = impedance
    if not math.isfinite(calibration_factor.real) or not math.isfinite(calibration_factor.imag):
        raise ValueError("LCR 复数校准系数不是有限数")
    if abs(calibration_factor) <= 1e-15:
        raise ValueError("LCR 复数校准系数过小")
    impedance *= calibration_factor
    impedance -= complex(
        config.series_resistance_ohm, config.series_reactance_ohm
    )
    admittance = 1.0 / impedance - complex(
        config.parallel_conductance_s, config.parallel_susceptance_s
    )
    if abs(admittance) <= 1e-18:
        raise ValueError("开路补偿后导纳过小，无法形成有限阻抗")
    impedance = 1.0 / admittance
    resistance = float(impedance.real)
    reactance = float(impedance.imag)
    magnitude = float(abs(impedance))
    phase_deg = float(np.rad2deg(np.angle(impedance)))
    omega = 2 * np.pi * frequency_hz
    final_admittance = 1.0 / impedance
    conductance = float(final_admittance.real)
    susceptance = float(final_admittance.imag)
    quality = abs(reactance) / max(abs(resistance), 1e-30)
    dissipation = 1.0 / max(quality, 1e-30)
    return {
        "frequency_hz": float(frequency_hz),
        "uncalibrated_impedance_real_ohm": float(uncalibrated_impedance.real),
        "uncalibrated_impedance_imag_ohm": float(uncalibrated_impedance.imag),
        "calibration_factor_real": float(calibration_factor.real),
        "calibration_factor_imag": float(calibration_factor.imag),
        "calibration_applied": bool(abs(calibration_factor - 1.0) > 1e-12),
        "impedance_real_ohm": resistance,
        "impedance_imag_ohm": reactance,
        "impedance_magnitude_ohm": magnitude,
        "phase_deg": phase_deg,
        "series_resistance_ohm": resistance,
        "series_capacitance_f": -1.0 / (omega * reactance) if reactance < 0 else float("nan"),
        "series_inductance_h": reactance / omega if reactance > 0 else float("nan"),
        "parallel_resistance_ohm": 1.0 / conductance if conductance > 0 else float("nan"),
        "parallel_capacitance_f": susceptance / omega if susceptance > 0 else float("nan"),
        "parallel_inductance_h": -1.0 / (omega * susceptance) if susceptance < 0 else float("nan"),
        "quality_factor": quality,
        "dissipation_factor": dissipation,
        "voltage_rms_v": float(abs(dut_voltage) / math.sqrt(2)),
        "current_rms_a": float(abs(dut_current) / math.sqrt(2)),
        "voltage_fit_r2": float(voltage_fit_r2),
        "current_fit_r2": float(current_fit_r2),
        "voltage_snr_db": float(voltage_snr_db),
        "current_snr_db": float(current_snr_db),
        "analysis_start_s": float(result.time_s[first]),
        "analysis_end_s": float(result.time_s[last - 1]),
        "analysis_cycles": int(cycles),
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


def aggregate_lcr_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[float, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(float(row["frequency_hz"]), []).append(row)
    result: list[dict[str, Any]] = []
    excluded = {"run_index", "repeat", "npz_file"}
    for frequency, group in sorted(groups.items()):
        summary: dict[str, Any] = {
            "frequency_hz": frequency,
            "repeats_completed": len(group),
        }
        for key, value in group[0].items():
            if key in excluded or not isinstance(value, (int, float)):
                continue
            values = np.asarray([float(item[key]) for item in group], dtype=np.float64)
            finite = values[np.isfinite(values)]
            summary[key] = float(np.mean(finite)) if finite.size else float("nan")
            summary[f"{key}_std"] = (
                float(np.std(finite, ddof=1)) if finite.size > 1 else 0.0
            )
        result.append(summary)
    return result


def write_resistor_calibration(
    path: str | Path,
    rows: list[dict[str, Any]],
    standard_resistance_ohm: float,
    base_config: AcquisitionConfig,
    lcr: LcrConfig,
    source_directory: Path,
) -> Path:
    """Build a per-frequency complex correction from a precision resistor run."""
    if not math.isfinite(standard_resistance_ohm) or standard_resistance_ohm <= 0:
        raise ValueError("精准电阻阻值必须是大于0的有限数")
    groups: dict[float, list[complex]] = {}
    for row in rows:
        measured = complex(
            float(row["uncalibrated_impedance_real_ohm"]),
            float(row["uncalibrated_impedance_imag_ohm"]),
        )
        if math.isfinite(measured.real) and math.isfinite(measured.imag):
            groups.setdefault(float(row["frequency_hz"]), []).append(measured)
    if not groups:
        raise ValueError("没有有效的精准电阻测量结果，无法生成校准")
    points: list[dict[str, Any]] = []
    for frequency, measured_values in sorted(groups.items()):
        measured = sum(measured_values, 0.0 + 0.0j) / len(measured_values)
        if abs(measured) <= 1e-15:
            raise ValueError(f"{frequency:g} Hz 的标准电阻测量阻抗过小")
        factor = standard_resistance_ohm / measured
        corrected = factor * measured
        points.append({
            "frequency_hz": frequency,
            "repeats": len(measured_values),
            "measured_real_ohm": float(measured.real),
            "measured_imag_ohm": float(measured.imag),
            "factor_real": float(factor.real),
            "factor_imag": float(factor.imag),
            "factor_magnitude": float(abs(factor)),
            "factor_phase_deg": float(np.rad2deg(np.angle(factor))),
            "corrected_real_ohm": float(corrected.real),
            "corrected_imag_ohm": float(corrected.imag),
        })
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "pico4824a-lcr-resistor-calibration-v1",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "standard_type": "precision_resistor",
        "standard_resistance_ohm": float(standard_resistance_ohm),
        "method": "complex_multiplier_Rref_over_Zmeasured",
        "source_directory": str(source_directory.resolve()),
        "signature": _calibration_signature(base_config, lcr),
        "points": points,
    }
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target.resolve()


def discover_lcr_calibrations(output_root: str | Path) -> list[dict[str, Any]]:
    """Return valid resistor calibration profiles, newest first."""
    root = Path(output_root)
    profiles: list[dict[str, Any]] = []
    if not root.is_dir():
        return profiles
    for path in root.glob("lcr_*/calibration.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            points = raw.get("points", [])
            frequencies = sorted(
                float(point["frequency_hz"])
                for point in points
                if isinstance(point, dict) and float(point.get("frequency_hz", 0)) > 0
            )
            if (
                raw.get("schema") != "pico4824a-lcr-resistor-calibration-v1"
                or not frequencies
            ):
                continue
            profiles.append({
                "path": str(path.resolve()),
                "created_at": str(raw.get("created_at", "")),
                "standard_resistance_ohm": float(raw["standard_resistance_ohm"]),
                "point_count": len(frequencies),
                "frequency_start_hz": frequencies[0],
                "frequency_stop_hz": frequencies[-1],
            })
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    profiles.sort(key=lambda item: (item["created_at"], item["path"]), reverse=True)
    return profiles


LcrProgress = Callable[[dict[str, Any], CaptureResult | None], None]


def execute_lcr(
    base_config: AcquisitionConfig,
    lcr: LcrConfig,
    device: Pico4824A,
    output_root: Path,
    stop_event: threading.Event,
    progress: LcrProgress | None = None,
    calibration_standard_ohm: float | None = None,
) -> LcrOutcome:
    base_config.validate()
    lcr.validate()
    for name in (lcr.voltage_channel, lcr.current_channel):
        if not base_config.channels[name].enabled:
            raise ValueError(f"LCR 通道 {name} 尚未启用")
    maximum_frequency = max(lcr.frequencies_hz)
    if base_config.sample_rate_hz / maximum_frequency < 8:
        raise ValueError("最高 LCR 频率至少需要每周期 8 个采样点")
    calibration_run = calibration_standard_ohm is not None
    if calibration_run:
        if not math.isfinite(float(calibration_standard_ohm)) or float(calibration_standard_ohm) <= 0:
            raise ValueError("精准电阻阻值必须是大于0的有限数")
        calibration = None
    elif lcr.calibration_enabled:
        calibration = load_lcr_calibration(lcr.calibration_file or "", base_config, lcr)
    else:
        calibration = None

    stamp = datetime.now().strftime("lcr_%Y%m%d_%H%M%S_%f")
    directory = output_root / stamp
    raw_dir = directory / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    (directory / "lcr_config.json").write_text(
        json.dumps(
            {"base_config": base_config.to_dict(), "lcr": asdict(lcr)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    rows: list[dict[str, Any]] = []
    total_runs = len(lcr.frequencies_hz) * lcr.repeats
    run_index = 0
    for point_index, frequency in enumerate(lcr.frequencies_hz, start=1):
        for repeat in range(1, lcr.repeats + 1):
            if stop_event.is_set():
                summary = aggregate_lcr_rows(rows)
                _write_csv(directory / "runs.csv", rows)
                _write_csv(directory / "summary.csv", summary)
                return LcrOutcome(directory, rows, summary, True)
            run_index += 1
            config = AcquisitionConfig.from_dict(base_config.to_dict())
            config.awg.enabled = True
            config.awg.waveform = "lcr_tone"
            config.awg.frequency_hz = frequency
            config.awg.cycles = lcr.burst_cycles
            config.awg.pk_to_pk_v = lcr.excitation_vpp
            config.awg.offset_v = 0.0
            config.awg.trigger_source = "software"
            config.awg.tone_ramp_cycles = lcr.ramp_cycles
            pre_duration = max(2.0 / frequency, 50e-6)
            post_duration = (lcr.burst_cycles + 5.0) / frequency
            config.pre_trigger_samples = max(1, round(config.sample_rate_hz * pre_duration))
            config.post_trigger_samples = max(2, round(config.sample_rate_hz * post_duration))
            config.trigger.enabled = True
            # No third AWG-monitor cable is needed: one of the two measured
            # front-end outputs also serves as the simple-trigger source.
            config.trigger.source = lcr.trigger_source_channel
            config.validate()
            if progress:
                progress(
                    {
                        "phase": "capturing",
                        "run_index": run_index,
                        "total_runs": total_runs,
                        "point_index": point_index,
                        "total_points": len(lcr.frequencies_hz),
                        "repeat": repeat,
                        "repeats": lcr.repeats,
                        "frequency_hz": frequency,
                    },
                    None,
                )
            try:
                capture = device.capture(config)
            except Exception:
                if stop_event.is_set():
                    summary = aggregate_lcr_rows(rows)
                    return LcrOutcome(directory, rows, summary, True)
                raise
            file_name = f"f{frequency:012.3f}_r{repeat:03d}.npz"
            path = save_npz(capture, raw_dir / file_name)
            factor = (
                calibration.factor_at(frequency)
                if calibration is not None
                else 1.0 + 0.0j
            )
            metrics = analyze_impedance(capture, lcr, frequency, factor)
            row = {
                "run_index": run_index,
                "repeat": repeat,
                "npz_file": str(path.relative_to(directory)),
                **metrics,
            }
            rows.append(row)
            summary = aggregate_lcr_rows(rows)
            _write_csv(directory / "runs.csv", rows)
            _write_csv(directory / "summary.csv", summary)
            if progress:
                progress(
                    {
                        "phase": "saved",
                        "run_index": run_index,
                        "total_runs": total_runs,
                        "point_index": point_index,
                        "total_points": len(lcr.frequencies_hz),
                        "repeat": repeat,
                        "repeats": lcr.repeats,
                        "frequency_hz": frequency,
                    },
                    capture,
                )
            if run_index < total_runs and stop_event.wait(lcr.interval_s):
                summary = aggregate_lcr_rows(rows)
                return LcrOutcome(directory, rows, summary, True)
    summary = aggregate_lcr_rows(rows)
    calibration_file: Path | None = None
    if calibration_run:
        calibration_file = write_resistor_calibration(
            directory / "calibration.json",
            rows,
            float(calibration_standard_ohm),
            base_config,
            lcr,
            directory,
        )
    return LcrOutcome(
        directory,
        rows,
        summary,
        False,
        calibration_file=calibration_file,
        calibration_standard_ohm=(
            float(calibration_standard_ohm) if calibration_run else None
        ),
    )
