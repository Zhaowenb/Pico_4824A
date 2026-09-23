"""Three-channel ATA large-signal linearity acquisition and offline analysis.

The capture path stores the unmodified ADC voltages in NPZ. All derived
metrics are written alongside those files and can be recalculated later
without opening the PicoScope or changing the saved source data.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable

import numpy as np

from .config import AcquisitionConfig, CHANNEL_NAMES, RANGE_VOLTS
from .device import CaptureResult, Pico4824A
from .storage import load_npz, save_npz


@dataclass(slots=True)
class LinearityConfig:
    frequency_hz: float = 60_000.0
    cycles: int = 5
    ramp_cycles: float = 1.0
    vpp_start: float = 0.2
    vpp_stop: float = 4.0
    vpp_step: float = 0.2
    direction: str = "up"
    repeats: int = 3
    interval_s: float = 0.5
    sample_rate_hz: float = 20_000_000.0
    capture_duration_us: float = 1000.0
    trigger_position_percent: float = 15.0
    trigger_signal: str = "voltage"
    trigger_level_v: float = 0.02
    voltage_channel: str = "A"
    voltage_range: str = "5V"
    current_channel: str = "B"
    current_range: str = "5V"
    receiver_channel: str = "C"
    receiver_range: str = "2V"
    voltage_scale_v_per_v: float = 50.0
    voltage_offset_v: float = 0.0
    current_scale_a_per_v: float = 0.5
    current_offset_v: float = 0.0
    current_polarity: int = 1
    ata_voltage_gain: float = 1.0
    noise_start_us: float = -120.0
    noise_end_us: float = -20.0
    direct_start_us: float = 0.0
    direct_end_us: float = 250.0
    echo_start_us: float = 500.0
    echo_end_us: float = 800.0
    harmonic_order: int = 5
    alignment_max_shift_us: float = 10.0
    minimum_snr_db: float = 6.0
    trip_detection_enabled: bool = True
    trip_drop_ratio: float = 0.20
    trip_hold_cycles: float = 1.0
    minimum_monitor_rms_v: float = 0.002
    low_current_reference_count: int = 3
    low_current_reference_max_a: float = 0.0
    force_origin: bool = False
    threshold_d_wave_pct: float = 10.0
    threshold_receiver_thd_pct: float = 5.0
    threshold_current_thd_pct: float = 5.0
    threshold_compression_db: float = 1.0
    threshold_kme_deviation_pct: float = 10.0
    threshold_correlation: float = 0.98
    alert_voltage_vpp_v: float = 400.0
    alert_current_peak_a: float = 2.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LinearityConfig":
        config = cls(**raw)
        config.validate()
        return config

    def validate(self) -> None:
        for name in ("voltage_channel", "current_channel", "receiver_channel"):
            value = str(getattr(self, name)).upper()
            if value not in CHANNEL_NAMES:
                raise ValueError(f"{name} 必须是 A–H 通道")
            setattr(self, name, value)
        if len({self.voltage_channel, self.current_channel, self.receiver_channel}) != 3:
            raise ValueError("Voltage Monitor、Current Monitor、Receiver 必须使用三个不同通道")
        for name in ("voltage_range", "current_range", "receiver_range"):
            if getattr(self, name) not in RANGE_VOLTS:
                raise ValueError(f"不支持的 Pico 输入量程：{getattr(self, name)}")
        if self.trigger_signal not in {"voltage", "current"}:
            raise ValueError("触发来源必须是 voltage 或 current")
        if self.direction not in {"up", "down", "both"}:
            raise ValueError("扫描方向必须是 up、down 或 both")
        finite_positive = {
            "frequency_hz": self.frequency_hz,
            "cycles": self.cycles,
            "vpp_start": self.vpp_start,
            "vpp_stop": self.vpp_stop,
            "vpp_step": self.vpp_step,
            "repeats": self.repeats,
            "interval_s": self.interval_s + 1e-12,
            "sample_rate_hz": self.sample_rate_hz,
            "capture_duration_us": self.capture_duration_us,
            "voltage_scale_v_per_v": self.voltage_scale_v_per_v,
            "current_scale_a_per_v": self.current_scale_a_per_v,
            "ata_voltage_gain": self.ata_voltage_gain,
            "minimum_monitor_rms_v": self.minimum_monitor_rms_v,
        }
        for label, value in finite_positive.items():
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{label} 必须是大于 0 的有限数")
        if self.frequency_hz > 1_000_000 or self.sample_rate_hz > 80_000_000:
            raise ValueError("频率或采样率超过 Pico 4824A 支持范围")
        if not 1 <= self.cycles <= 10_000 or self.ramp_cycles < 0 or self.ramp_cycles * 2 >= self.cycles:
            raise ValueError("Burst 周期/缓升缓降参数无效")
        if not 0 < self.vpp_start <= self.vpp_stop <= 4 or self.vpp_step <= 0:
            raise ValueError("AWG Vpp 扫描必须满足 0 < 起始 ≤ 停止 ≤ 4 Vpp，且步长大于 0")
        if self.vpp_point_count > 500 or self.vpp_point_count * self.repeats * (2 if self.direction == "both" else 1) > 10_000:
            raise ValueError("一次线性度测试最多允许 10000 次采集")
        if not 0 <= self.trigger_position_percent < 100:
            raise ValueError("触发位置必须在 0–100% 之间")
        available_after_trigger_us = self.capture_duration_us * (1.0 - self.trigger_position_percent / 100.0)
        burst_duration_us = self.cycles / self.frequency_hz * 1e6
        if self.direct_end_us > available_after_trigger_us:
            raise ValueError("Direct Wave 时间窗终点超出触发后的采集时长")
        if burst_duration_us > available_after_trigger_us:
            raise ValueError("采集时长不足以完整记录设定的 AWG Burst；请增加采集时长或触发后比例")
        available_before_trigger_us = self.capture_duration_us * self.trigger_position_percent / 100.0
        if self.noise_start_us < -available_before_trigger_us:
            raise ValueError("Noise 时间窗起点超出触发前的采集时长")
        if not 2 <= self.harmonic_order <= 15 or self.low_current_reference_count < 2:
            raise ValueError("谐波阶数至少 2，低电流参考点数至少 2")
        highest_harmonic_samples = self.sample_rate_hz / (
            self.frequency_hz * self.harmonic_order
        )
        if highest_harmonic_samples < 4:
            raise ValueError(
                "采样率不足以分析所选最高谐波：最高谐波至少需要 4 个采样点/周期；"
                "请提高采样率、降低频率或降低谐波阶数"
            )
        if self.cycles - 2 * self.ramp_cycles < 2:
            raise ValueError("缓升缓降之外至少需要保留 2 个平顶周期用于谐波分析")
        for start, end, label in (
            (self.noise_start_us, self.noise_end_us, "Noise"),
            (self.direct_start_us, self.direct_end_us, "Direct Wave"),
            (self.echo_start_us, self.echo_end_us, "Echo"),
        ):
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                raise ValueError(f"{label} 时间窗无效")
        if self.noise_end_us > self.direct_start_us:
            raise ValueError("Noise Window 结束时间必须不晚于 Direct Wave Window 起点")
        if self.alignment_max_shift_us < 0 or self.minimum_snr_db < 0:
            raise ValueError("对齐范围和最低 SNR 不能为负")
        if not 0.02 <= self.trip_drop_ratio <= 0.8 or not 1 <= self.trip_hold_cycles <= 20:
            raise ValueError("疑似跳闸阈值或持续周期超出范围")
        if not 0 < self.threshold_correlation <= 1:
            raise ValueError("Correlation 工程阈值必须在 (0, 1] 内")
        if not math.isfinite(self.alert_voltage_vpp_v) or self.alert_voltage_vpp_v <= 0 or not math.isfinite(self.alert_current_peak_a) or self.alert_current_peak_a <= 0:
            raise ValueError("程序提醒阈值必须为正数")
        for key in (self.threshold_d_wave_pct, self.threshold_receiver_thd_pct,
                    self.threshold_current_thd_pct, self.threshold_compression_db,
                    self.threshold_kme_deviation_pct):
            if not math.isfinite(float(key)) or key < 0:
                raise ValueError("工程阈值必须是有限非负数")

    @property
    def vpp_values(self) -> tuple[float, ...]:
        count = int(math.floor((self.vpp_stop - self.vpp_start) / self.vpp_step + 1e-9)) + 1
        values = [self.vpp_start + n * self.vpp_step for n in range(count)]
        if not values or values[-1] < self.vpp_stop - max(1e-9, self.vpp_stop * 1e-12):
            values.append(self.vpp_stop)
        return tuple(float(min(value, self.vpp_stop)) for value in values)

    @property
    def vpp_point_count(self) -> int:
        return len(self.vpp_values)

    @property
    def scan_plan(self) -> tuple[tuple[str, float], ...]:
        ascending = self.vpp_values
        if self.direction == "up":
            return tuple(("up", value) for value in ascending)
        if self.direction == "down":
            return tuple(("down", value) for value in reversed(ascending))
        return tuple(("up", value) for value in ascending) + tuple(("down", value) for value in reversed(ascending))


LinearityProgress = Callable[[dict[str, Any], CaptureResult | None], None]
LinearityTripHandler = Callable[[dict[str, Any]], bool]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})


def _window(time_s: np.ndarray, start_us: float, end_us: float) -> np.ndarray:
    return (time_s >= start_us * 1e-6) & (time_s <= end_us * 1e-6)


def _harmonics(time_s: np.ndarray, signal: np.ndarray, frequency_hz: float, order: int) -> dict[str, Any]:
    """Weighted known-frequency LS fit; Hann weights are consistent for every point."""
    if time_s.size < 2:
        raise ValueError("所选时窗采样不足以拟合谐波")
    sample_rate_hz = 1.0 / float(np.median(np.diff(time_s)))
    highest_harmonic_hz = frequency_hz * order
    if sample_rate_hz <= 2.0 * highest_harmonic_hz:
        raise ValueError(
            f"采样率 {sample_rate_hz:g} Hz 未满足第 {order} 次谐波 {highest_harmonic_hz:g} Hz 的奈奎斯特条件"
        )
    # Keep the fit bounded in size without downsampling below four points per
    # period of the highest requested harmonic.
    maximum_stride = max(1, int(sample_rate_hz // (4.0 * highest_harmonic_hz)))
    stride = min(max(1, int(math.ceil(time_s.size / 50_000))), maximum_stride)
    t = np.asarray(time_s[::stride], dtype=np.float64)
    y = np.asarray(signal[::stride], dtype=np.float64)
    if t.size < max(40, 8 * order):
        raise ValueError("所选时窗采样不足以拟合谐波")
    weights = np.hanning(t.size)
    if not np.any(weights):
        weights = np.ones(t.size)
    phase = 2 * np.pi * frequency_hz * t
    columns = [np.ones(t.size)]
    for harmonic in range(1, order + 1):
        columns.extend((np.cos(harmonic * phase), np.sin(harmonic * phase)))
    design = np.column_stack(columns)
    root_w = np.sqrt(weights)
    coefficients, *_ = np.linalg.lstsq(design * root_w[:, None], y * root_w, rcond=None)
    fitted = design @ coefficients
    rms = [float(math.hypot(coefficients[2 * k - 1], coefficients[2 * k]) / math.sqrt(2)) for k in range(1, order + 1)]
    fundamental = rms[0]
    if fundamental <= 1e-15:
        raise ValueError("基波过小")
    return {
        "fundamental_rms": fundamental,
        "thd_pct": float(100 * math.sqrt(sum(value * value for value in rms[1:])) / fundamental),
        "harmonics_rms": rms,
        "h2_h1_pct": float(100 * rms[1] / fundamental) if len(rms) >= 2 else None,
        "h3_h1_pct": float(100 * rms[2] / fundamental) if len(rms) >= 3 else None,
        "residual_rms": float(np.sqrt(np.average((y - fitted) ** 2, weights=weights))),
    }


def _moving_rms_envelope(
    capture: CaptureResult,
    channel: str,
    frequency_hz: float,
    window_cycles: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a phase-insensitive, decimated RMS envelope and its center times."""
    time_s = np.asarray(capture.time_s, dtype=np.float64)
    signal = np.asarray(capture.volts[channel], dtype=np.float64)
    if time_s.size != signal.size or time_s.size < 4:
        raise ValueError(f"{channel} 通道没有足够采样用于包络分析")
    sample_rate_hz = 1.0 / float(np.median(np.diff(time_s)))
    samples_per_cycle = sample_rate_hz / frequency_hz
    window_samples = max(4, int(round(samples_per_cycle * window_cycles)))
    if window_samples > signal.size:
        raise ValueError(f"{channel} 通道采集长度不足一个分析窗")
    centered = signal - float(np.median(signal))
    power_sum = np.concatenate(([0.0], np.cumsum(centered * centered, dtype=np.float64)))
    envelope = np.sqrt(
        np.maximum(0.0, (power_sum[window_samples:] - power_sum[:-window_samples]) / window_samples)
    )
    stride = max(1, int(round(samples_per_cycle / 8.0)))
    starts = np.arange(0, envelope.size, stride, dtype=np.int64)
    centers = (time_s[starts] + time_s[starts + window_samples - 1]) / 2.0
    return centers, envelope[starts]


def _region_containing(mask: np.ndarray, pivot: int) -> tuple[int, int] | None:
    """Return the inclusive bounds of the true run containing ``pivot``."""
    if pivot < 0 or pivot >= mask.size or not mask[pivot]:
        return None
    start = pivot
    end = pivot
    while start > 0 and mask[start - 1]:
        start -= 1
    while end + 1 < mask.size and mask[end + 1]:
        end += 1
    return start, end


def _monitor_windows(
    capture: CaptureResult,
    config: LinearityConfig,
) -> dict[str, Any]:
    """Locate the burst from its measured envelope and select its flat center.

    Trigger time is not assumed to be AWG burst time.  The analysis window is
    centered on the joint Voltage/Current Monitor plateau and sized to an
    integer number of cycles that excludes the programmed ramps.
    """
    profiles = {
        channel: _moving_rms_envelope(capture, channel, config.frequency_hz)
        for channel in (config.voltage_channel, config.current_channel)
    }
    time_s = profiles[config.voltage_channel][0]
    if not np.allclose(time_s, profiles[config.current_channel][0], rtol=0.0, atol=1e-15):
        raise ValueError("Voltage / Current Monitor 包络时间轴不一致")
    normalized = []
    for channel in (config.voltage_channel, config.current_channel):
        envelope = profiles[channel][1]
        peak = float(np.max(envelope))
        if not math.isfinite(peak) or peak <= 1e-12:
            raise ValueError(f"{channel} Monitor 未检测到可用于分析的突发信号")
        normalized.append(envelope / peak)
    joint = np.minimum(normalized[0], normalized[1])
    peak_index = int(np.argmax(joint))
    region = _region_containing(joint >= float(joint[peak_index]) * 0.90, peak_index)
    if region is None:
        raise ValueError("无法定位 Voltage / Current Monitor 的共同平顶区")
    first, last = region
    flat_cycles = config.cycles - 2.0 * config.ramp_cycles
    requested_cycles = int(math.floor(flat_cycles + 1e-9))
    envelope_step_s = float(np.median(np.diff(time_s))) if time_s.size > 1 else 0.0
    available_cycles = int(math.floor(
        max(0.0, (time_s[last] - time_s[first] + envelope_step_s) * config.frequency_hz - 0.20)
    ))
    fit_cycles = min(requested_cycles, available_cycles)
    if fit_cycles < 2:
        raise ValueError(
            "Monitor 平顶区不足 2 个完整周期，无法可靠计算谐波；请增加 Burst 周期或缩短缓升缓降"
        )
    center_s = float((time_s[first] + time_s[last]) / 2.0)
    analysis_start_s = center_s - fit_cycles / (2.0 * config.frequency_hz)
    analysis_end_s = center_s + fit_cycles / (2.0 * config.frequency_hz)
    burst_start_s = center_s - config.cycles / (2.0 * config.frequency_hz)
    burst_end_s = center_s + config.cycles / (2.0 * config.frequency_hz)
    capture_time = np.asarray(capture.time_s, dtype=np.float64)
    if burst_start_s < capture_time[0] or burst_end_s > capture_time[-1]:
        raise ValueError("推定的完整 Burst 超出本次采集范围；请增加触发前/后采样时间")
    return {
        "burst_center_s": center_s,
        "burst_start_s": burst_start_s,
        "burst_end_s": burst_end_s,
        "analysis_start_s": analysis_start_s,
        "analysis_end_s": analysis_end_s,
        "analysis_cycles": fit_cycles,
    }


def _snr(signal: np.ndarray, noise: np.ndarray) -> float | None:
    if signal.size == 0 or noise.size == 0:
        return None
    noise_rms = float(np.sqrt(np.mean(np.square(noise))))
    signal_rms = float(np.sqrt(np.mean(np.square(signal))))
    if noise_rms <= 1e-15:
        return 300.0 if signal_rms > 0 else None
    return float(20 * math.log10(max(signal_rms, 1e-30) / noise_rms))


def _suspected_trip(capture: CaptureResult, config: LinearityConfig) -> dict[str, Any]:
    """Detect sustained collapse from measured envelopes, not trigger-relative cycles."""
    if not config.trip_detection_enabled:
        return {"suspected_trip": False, "monitor_state": "TRIP_CHECK_DISABLED",
                "monitor_message": "疑似跳闸检查已关闭"}
    flat_cycles = config.cycles - 2.0 * config.ramp_cycles
    if flat_cycles < 2:
        return {"suspected_trip": False, "monitor_state": "TRIP_CHECK_UNASSESSED",
                "monitor_message": "可用平顶周期不足，无法可靠判别持续掉幅"}
    hold_s = config.trip_hold_cycles / config.frequency_hz
    results: list[tuple[str, float]] = []
    unassessed_messages: list[str] = []
    for label, channel in (("Voltage", config.voltage_channel),
                           ("Current", config.current_channel)):
        try:
            envelope_time, envelope = _moving_rms_envelope(
                capture, channel, config.frequency_hz, window_cycles=0.5
            )
        except ValueError as exc:
            unassessed_messages.append(str(exc))
            continue
        peak = float(np.max(envelope)) if envelope.size else 0.0
        if not math.isfinite(peak) or peak < config.minimum_monitor_rms_v:
            unassessed_messages.append(f"{label} Monitor 未达到有效幅值，无法判别掉幅")
            continue
        high_indices = np.flatnonzero(envelope >= peak * 0.80)
        if not high_indices.size:
            continue
        first_high = int(high_indices[0])
        if first_high == 0:
            unassessed_messages.append(f"{label} Monitor 突发起始未完整记录，无法可靠判别掉幅")
            continue
        # The half-cycle RMS reaches 80% just before the flat top. Stop the
        # decision window before the configured natural fade-out.
        check_end_s = envelope_time[first_high] + flat_cycles / config.frequency_hz
        in_check = ((np.arange(envelope.size) >= first_high)
                    & (envelope_time <= check_end_s))
        check_indices = np.flatnonzero(in_check)
        if not check_indices.size:
            continue
        step_s = float(np.median(np.diff(envelope_time))) if envelope_time.size > 1 else 0.0
        if envelope_time[check_indices[-1]] - envelope_time[first_high] + step_s < hold_s:
            continue
        low = envelope[check_indices] < peak * config.trip_drop_ratio
        low_positions = np.flatnonzero(low)
        if not low_positions.size:
            continue
        run_start = int(low_positions[0])
        run_end = run_start
        for position in list(low_positions[1:]) + [None]:
            if position is not None and int(position) == run_end + 1:
                run_end = int(position)
                continue
            first_low = int(check_indices[run_start])
            last_low = int(check_indices[run_end])
            if envelope_time[last_low] - envelope_time[first_low] + step_s >= hold_s:
                ratio = float(np.min(envelope[check_indices[run_start:run_end + 1]]) / peak)
                results.append((label, ratio))
                break
            if position is not None:
                run_start = int(position)
                run_end = int(position)
    if results:
        label, ratio = min(results, key=lambda item: item[1])
        return {"suspected_trip": True, "monitor_state": "SUSPECT_TRIP",
                "monitor_min_ratio": ratio,
                "monitor_message": f"{label} Monitor 连续掉幅（最低剩余幅值 {ratio:.1%}）；仅为波形疑似跳闸"}
    if unassessed_messages:
        return {"suspected_trip": False, "monitor_state": "MONITOR_INVALID",
                "monitor_message": "；".join(unassessed_messages)}
    return {"suspected_trip": False, "monitor_state": "OK",
            "monitor_message": "未检测到持续 Monitor 掉幅"}


def _channel_clips(capture: CaptureResult, channel: str) -> bool:
    scale = RANGE_VOLTS[capture.config.channels[channel].range]
    return bool(np.any(np.abs(capture.volts[channel]) >= 0.98 * scale))


def _raw_path(directory: Path, row: dict[str, Any]) -> Path:
    relative = Path(str(row.get("raw_file", row.get("npz_file", ""))))
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".npz":
        raise ValueError("原始波形路径不合法")
    target = (directory / relative).resolve()
    if not target.is_relative_to((directory / "raw").resolve()) or not target.is_file():
        raise ValueError("原始波形必须位于所选目录的 raw 子目录")
    return target


def _base_run(capture: CaptureResult, config: LinearityConfig, row: dict[str, Any], recalculate: bool = False) -> dict[str, Any]:
    frequency = float(row["frequency_hz"])
    vch, ich, rch = config.voltage_channel, config.current_channel, config.receiver_channel
    t = capture.time_s
    raw_v = capture.volts[vch] - config.voltage_offset_v
    raw_i = (capture.volts[ich] - config.current_offset_v) * config.current_polarity
    raw_rx = capture.volts[rch]
    noise_mask = _window(t, config.noise_start_us, config.noise_end_us)
    direct_mask = _window(t, config.direct_start_us, config.direct_end_us)
    echo_mask = _window(t, config.echo_start_us, config.echo_end_us)
    if np.count_nonzero(direct_mask) < max(40, config.harmonic_order * 8):
        raise ValueError("Direct Wave Window 内采样不足")
    baseline_v = float(np.median(raw_v[noise_mask])) if np.any(noise_mask) else 0.0
    baseline_i = float(np.median(raw_i[noise_mask])) if np.any(noise_mask) else 0.0
    baseline_rx = float(np.median(raw_rx[noise_mask])) if np.any(noise_mask) else 0.0
    v = (raw_v - baseline_v) * config.voltage_scale_v_per_v
    i = (raw_i - baseline_i) * config.current_scale_a_per_v
    rx = raw_rx - baseline_rx
    vclips = _channel_clips(capture, vch)
    iclips = _channel_clips(capture, ich)
    rxclips = _channel_clips(capture, rch)
    clipped_channels = [name for name, clipped in ((vch, vclips), (ich, iclips), (rch, rxclips)) if clipped]
    overflow_channels = list(capture.overflow_channels)
    clipped = bool(clipped_channels)
    overflow = bool(overflow_channels)
    valid = not clipped and not overflow
    output = {
        **row,
        "raw_file": row.get("raw_file", row.get("npz_file", "")),
        "overflow": overflow,
        "overflow_channels": overflow_channels,
        "clipped": clipped,
        "clipped_channels": clipped_channels,
        "valid": valid,
        "status": "OK" if valid else ("CLIPPED" if clipped else "OVERFLOW"),
        "voltage_peak_v": None,
        "voltage_pp_v": None,
        "voltage_rms_v": None,
        "voltage_fundamental_rms_v": None,
        "current_peak_a": None,
        "current_pp_a": None,
        "current_rms_a": None,
        "current_fundamental_rms_a": None,
        "receiver_peak_v": None,
        "receiver_pp_v": None,
        "receiver_rms_v": None,
        "receiver_fundamental_rms_v": None,
        "receiver_thd_pct": None,
        "current_thd_pct": None,
        "voltage_thd_pct": None,
        "receiver_snr_db": None,
        "current_snr_db": None,
        "K_ME_v_per_a": None,
        "D_wave_pct": None,
        "correlation": None,
        "compression_db": None,
        "aligned_shift_us": None,
        "receiver_harmonics_rms_v": None,
        "current_harmonics_rms_a": None,
        "echo_peak_v": None,
        "echo_pp_v": None,
        "echo_rms_v": None,
        "echo_fundamental_rms_v": None,
        "echo_thd_pct": None,
        "echo_snr_db": None,
        "echo_harmonics_rms_v": None,
        "D_echo_pct": None,
        "echo_correlation": None,
        "safety_state": "OK",
        "safety_message": "未超过程序提醒阈值",
        "voltage_snr_db": None,
        "monitor_valid": False,
        "monitor_state": "UNASSESSED",
        "monitor_message": "Monitor 有效性尚未判别",
        "suspected_trip": None,
    }
    if not valid:
        output.update({"monitor_state": "UNASSESSED_ADC_INVALID",
                       "monitor_message": "ADC clipping 或 overflow 导致 Monitor/Receiver 指标无效",
                       "suspected_trip": None})
        return output
    monitor_health = _suspected_trip(capture, config)
    output.update(monitor_health)
    try:
        monitor_window = _monitor_windows(capture, config)
    except ValueError as exc:
        output["valid"] = False
        output["status"] = "SUSPECT_TRIP" if monitor_health.get("suspected_trip") else "MONITOR_INVALID"
        output["analysis_message"] = str(exc)
        return output
    burst_mask = ((t >= monitor_window["burst_start_s"])
                  & (t <= monitor_window["burst_end_s"]))
    monitor_analysis_mask = ((t >= monitor_window["analysis_start_s"])
                             & (t <= monitor_window["analysis_end_s"]))
    if np.count_nonzero(burst_mask) < max(40, config.harmonic_order * 8):
        output["valid"] = False
        output["status"] = "SUSPECT_TRIP" if monitor_health.get("suspected_trip") else "MONITOR_INVALID"
        output["analysis_message"] = "完整 Burst 窗口内采样不足"
        return output
    if np.count_nonzero(monitor_analysis_mask) < max(40, config.harmonic_order * 8):
        output["valid"] = False
        output["status"] = "SUSPECT_TRIP" if monitor_health.get("suspected_trip") else "MONITOR_INVALID"
        output["analysis_message"] = "Monitor 平顶分析窗采样不足"
        return output
    vburst, iburst = v[burst_mask], i[burst_mask]
    rx_direct, rx_noise = rx[direct_mask], rx[noise_mask]
    voltage_noise = v[noise_mask]
    current_noise = i[noise_mask]
    try:
        vm = _harmonics(t[monitor_analysis_mask], v[monitor_analysis_mask], frequency, config.harmonic_order)
        im = _harmonics(t[monitor_analysis_mask], i[monitor_analysis_mask], frequency, config.harmonic_order)
        rm = _harmonics(t[direct_mask], rx_direct, frequency, config.harmonic_order)
    except ValueError as exc:
        output["valid"] = False
        output["status"] = "SUSPECT_TRIP" if monitor_health.get("suspected_trip") else "INVALID"
        output["analysis_message"] = str(exc)
        return output
    echo_available = bool(np.count_nonzero(echo_mask) >= max(40, config.harmonic_order * 8))
    echo_metrics = None
    if echo_available:
        try:
            echo_metrics = _harmonics(t[echo_mask], rx[echo_mask], frequency, config.harmonic_order)
        except ValueError:
            echo_available = False
    output.update({
        "voltage_peak_v": float(np.max(np.abs(vburst))),
        "voltage_pp_v": float(np.ptp(vburst)),
        "voltage_rms_v": float(np.sqrt(np.mean(vburst ** 2))),
        "voltage_fundamental_rms_v": vm["fundamental_rms"],
        "voltage_h2_h1_pct": vm["h2_h1_pct"], "voltage_h3_h1_pct": vm["h3_h1_pct"],
        "voltage_thd_pct": vm["thd_pct"],
        "voltage_snr_db": _snr(v[monitor_analysis_mask], voltage_noise),
        "current_peak_a": float(np.max(np.abs(iburst))),
        "current_pp_a": float(np.ptp(iburst)),
        "current_rms_a": float(np.sqrt(np.mean(iburst ** 2))),
        "current_fundamental_rms_a": im["fundamental_rms"],
        "current_h2_h1_pct": im["h2_h1_pct"], "current_h3_h1_pct": im["h3_h1_pct"],
        "current_thd_pct": im["thd_pct"],
        "receiver_peak_v": float(np.max(np.abs(rx_direct))),
        "receiver_pp_v": float(np.ptp(rx_direct)),
        "receiver_rms_v": float(np.sqrt(np.mean(rx_direct ** 2))),
        "receiver_fundamental_rms_v": rm["fundamental_rms"],
        "receiver_h2_h1_pct": rm["h2_h1_pct"], "receiver_h3_h1_pct": rm["h3_h1_pct"],
        "receiver_thd_pct": rm["thd_pct"],
        "receiver_snr_db": _snr(rx_direct, rx_noise),
        "current_snr_db": _snr(i[monitor_analysis_mask], current_noise),
        "K_ME_v_per_a": float(np.max(np.abs(rx_direct)) / max(np.max(np.abs(iburst)), 1e-30)),
        "receiver_harmonics_rms_v": rm["harmonics_rms"],
        "current_harmonics_rms_a": im["harmonics_rms"],
        "monitor_analysis_start_us": monitor_window["analysis_start_s"] * 1e6,
        "monitor_analysis_end_us": monitor_window["analysis_end_s"] * 1e6,
        "monitor_analysis_cycles": monitor_window["analysis_cycles"],
        "direct_samples": int(np.count_nonzero(direct_mask)),
        "echo_available": echo_available,
        "echo_peak_v": float(np.max(np.abs(rx[echo_mask]))) if echo_available else None,
        "echo_pp_v": float(np.ptp(rx[echo_mask])) if echo_available else None,
        "echo_rms_v": float(np.sqrt(np.mean(rx[echo_mask] ** 2))) if echo_available else None,
        "echo_fundamental_rms_v": echo_metrics["fundamental_rms"] if echo_metrics else None,
        "echo_thd_pct": echo_metrics["thd_pct"] if echo_metrics else None,
        "echo_snr_db": _snr(rx[echo_mask], rx_noise) if echo_available else None,
        "echo_harmonics_rms_v": echo_metrics["harmonics_rms"] if echo_metrics else None,
    })
    voltage_alert = float(output["voltage_pp_v"]) > config.alert_voltage_vpp_v
    current_alert = float(output["current_peak_a"]) > config.alert_current_peak_a
    if voltage_alert or current_alert:
        output["safety_state"] = "WARN"
        warnings = []
        if voltage_alert:
            warnings.append(f"Voltage Monitor 换算结果 {output['voltage_pp_v']:.4g} Vpp 超过 {config.alert_voltage_vpp_v:g} Vpp 程序提醒")
        if current_alert:
            warnings.append(f"Current Monitor 换算结果 {output['current_peak_a']:.4g} Apeak 超过 {config.alert_current_peak_a:g} Apeak 程序提醒")
        output["safety_message"] = "；".join(warnings)
    monitor_quality = all(
        value is not None and math.isfinite(float(value)) and float(value) >= config.minimum_snr_db
        for value in (output.get("voltage_snr_db"), output.get("current_snr_db"))
    )
    output["monitor_valid"] = bool(monitor_quality and monitor_health["monitor_state"] not in {"MONITOR_INVALID", "SUSPECT_TRIP"})
    if monitor_health["monitor_state"] == "SUSPECT_TRIP":
        output["valid"] = False
        output["status"] = "SUSPECT_TRIP"
    elif not output["monitor_valid"]:
        output["monitor_state"] = "MONITOR_INVALID"
        output["monitor_message"] = "Voltage 或 Current Monitor 的 SNR 未达到最低有效要求"
        output["valid"] = False
        output["status"] = "MONITOR_INVALID"
    snr = output.get("receiver_snr_db")
    if (snr is None or snr < config.minimum_snr_db) and output["status"] not in {"SUSPECT_TRIP", "MONITOR_INVALID"}:
        output["valid"] = False
        output["status"] = "LOW_SNR"
    return output


def _wave_from_capture(capture: CaptureResult, channel: str, start_us: float, end_us: float, max_points: int = 5000) -> tuple[np.ndarray, np.ndarray]:
    mask = _window(capture.time_s, start_us, end_us)
    indices = np.flatnonzero(mask)
    if not indices.size:
        raise ValueError("波形窗外没有采样点")
    step = max(1, math.ceil(indices.size / max_points))
    indices = indices[::step]
    y = np.asarray(capture.volts[channel][indices], dtype=np.float64)
    y = y - float(np.mean(y))
    return capture.time_s[indices], y


def _compare_waveforms(reference_t: np.ndarray, reference: np.ndarray, current_t: np.ndarray, current: np.ndarray, max_shift_us: float) -> tuple[float, float, float]:
    if reference.size < 20 or current.size < 20:
        raise ValueError("波形形状比较采样不足")
    step = max(float(np.median(np.diff(reference_t))), float(np.median(np.diff(current_t))))
    start = max(reference_t[0], current_t[0])
    end = min(reference_t[-1], current_t[-1])
    if end <= start:
        raise ValueError("参考波形与当前波形没有共同有效时间窗")
    grid = np.arange(start, end, step, dtype=np.float64)
    if grid.size > 20_000:
        grid = grid[::math.ceil(grid.size / 20_000)]
        step *= math.ceil((end - start) / (step * grid.size))
    ref = np.interp(grid, reference_t, reference)
    cur = np.interp(grid, current_t, current)
    ref -= np.mean(ref)
    cur -= np.mean(cur)
    max_shift = max(0, int(round(max_shift_us * 1e-6 / step)))
    best = (-math.inf, 0, 0.0, 0.0)
    for shift in range(-max_shift, max_shift + 1):
        if shift < 0:
            a, b = ref[-shift:], cur[:shift]
        elif shift > 0:
            a, b = ref[:-shift], cur[shift:]
        else:
            a, b = ref, cur
        if a.size < 20:
            continue
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        corr = float(np.dot(a, b) / denom) if denom else -1.0
        if corr > best[0]:
            amplitude = float(np.dot(a, b) / max(np.dot(a, a), 1e-30))
            residual = float(np.linalg.norm(b - amplitude * a) / max(np.linalg.norm(amplitude * a), 1e-30) * 100)
            best = (corr, shift, residual, amplitude)
    if not math.isfinite(best[0]):
        raise ValueError("未能完成波形对齐")
    return best[2], best[0], best[1] * step * 1e6


def _set_engineering_state(row: dict[str, Any], cfg: LinearityConfig) -> None:
    if not row.get("valid"):
        row["engineering_state"] = "无效数据"
        return
    deviations = [
        abs(float(row.get("K_ME_deviation_pct", 0) or 0)) > cfg.threshold_kme_deviation_pct,
        abs(float(row.get("D_wave_pct", 0) or 0)) > cfg.threshold_d_wave_pct,
        float(row.get("receiver_thd_pct", 0) or 0) > cfg.threshold_receiver_thd_pct,
        float(row.get("current_thd_pct", 0) or 0) > cfg.threshold_current_thd_pct,
        abs(float(row.get("compression_db", 0) or 0)) > cfg.threshold_compression_db,
        float(row.get("correlation", 1) if row.get("correlation") is not None else 1) < cfg.threshold_correlation,
    ]
    count = sum(deviations)
    row["engineering_state"] = "近似线性" if count == 0 else ("过渡" if count <= 2 else "明显偏离线性")


def _apply_models(folder: Path, runs: list[dict[str, Any]], cfg: LinearityConfig) -> None:
    valid = [row for row in runs if row.get("valid")]
    by_freq: dict[float, list[dict[str, Any]]] = {}
    for row in valid:
        by_freq.setdefault(float(row["frequency_hz"]), []).append(row)
    for frequency, group in by_freq.items():
        refs = [row for row in group if float(row["awg_vpp"]) == min(float(item["awg_vpp"]) for item in group)]
        reference = next((row for row in refs if float(row.get("receiver_snr_db") or -math.inf) >= cfg.minimum_snr_db), None)
        if reference is not None:
            reference_capture = load_npz(_raw_path(folder, reference))
            ref_t, ref_y = _wave_from_capture(reference_capture, cfg.receiver_channel, cfg.direct_start_us, cfg.direct_end_us)
            echo_reference = bool(reference.get("echo_available")) and float(reference.get("echo_snr_db") or -math.inf) >= cfg.minimum_snr_db
            if echo_reference:
                echo_ref_t, echo_ref_y = _wave_from_capture(reference_capture, cfg.receiver_channel, cfg.echo_start_us, cfg.echo_end_us)
            for row in group:
                try:
                    capture = load_npz(_raw_path(folder, row))
                    t, y = _wave_from_capture(capture, cfg.receiver_channel, cfg.direct_start_us, cfg.direct_end_us)
                    distortion, correlation, shift = _compare_waveforms(ref_t, ref_y, t, y, cfg.alignment_max_shift_us)
                    row.update({"D_wave_pct": distortion, "correlation": correlation, "aligned_shift_us": shift,
                                "reference_awg_vpp": reference["awg_vpp"], "reference_current_peak_a": reference.get("current_peak_a"),
                                "reference_raw_file": reference["raw_file"]})
                    if echo_reference and row.get("echo_available"):
                        echo_t, echo_y = _wave_from_capture(capture, cfg.receiver_channel, cfg.echo_start_us, cfg.echo_end_us)
                        d_echo, echo_corr, _echo_shift = _compare_waveforms(echo_ref_t, echo_ref_y, echo_t, echo_y, cfg.alignment_max_shift_us)
                        row.update({"D_echo_pct": d_echo, "echo_correlation": echo_corr,
                                    "echo_reference_awg_vpp": reference["awg_vpp"],
                                    "echo_reference_raw_file": reference["raw_file"]})
                except (OSError, ValueError, KeyError) as exc:
                    row["analysis_message"] = str(exc)
        by_point: dict[str, list[dict[str, Any]]] = {}
        for row in group:
            by_point.setdefault(str(row.get("direction", "up")), []).append(row)
        currents = np.asarray([float(row["current_peak_a"]) for row in group], dtype=float)
        amplitudes = np.asarray([float(row["receiver_peak_v"]) for row in group], dtype=float)
        if cfg.low_current_reference_max_a > 0:
            selected = currents <= cfg.low_current_reference_max_a
        else:
            threshold = np.sort(currents)[min(cfg.low_current_reference_count, len(currents)) - 1]
            selected = currents <= threshold
        if np.count_nonzero(selected) >= 2:
            if cfg.force_origin:
                slope = float(np.dot(currents[selected], amplitudes[selected]) / max(np.dot(currents[selected], currents[selected]), 1e-30))
                intercept = 0.0
            else:
                x = currents[selected]
                y = amplitudes[selected]
                x_center, y_center = float(np.mean(x)), float(np.mean(y))
                slope = float(np.dot(x - x_center, y - y_center) / max(np.dot(x - x_center, x - x_center), 1e-30))
                intercept = y_center - slope * x_center
            for row in group:
                predicted = slope * float(row["current_peak_a"]) + intercept
                measured = float(row["receiver_peak_v"])
                row["compression_db"] = float(20 * math.log10(max(measured, 1e-30) / max(predicted, 1e-30)))
                row["compression_fit_slope_v_per_a"] = slope
                row["compression_fit_intercept_v"] = intercept
        for row in group:
            direction_rows = by_point.get(str(row.get("direction", "up")), [])
            if direction_rows:
                direction_rows.sort(key=lambda item: float(item.get("current_peak_a") or math.inf))
                if cfg.low_current_reference_max_a > 0:
                    reference_rows = [item for item in direction_rows if float(item.get("current_peak_a") or math.inf) <= cfg.low_current_reference_max_a]
                else:
                    unique_currents = sorted({float(item["current_peak_a"]) for item in direction_rows if item.get("current_peak_a") is not None})
                    ref_max = unique_currents[min(cfg.low_current_reference_count, len(unique_currents)) - 1] if unique_currents else 0.0
                    reference_rows = [item for item in direction_rows if float(item.get("current_peak_a") or math.inf) <= ref_max]
                reference_k = [float(item["K_ME_v_per_a"]) for item in reference_rows if item.get("K_ME_v_per_a") is not None]
                ref_k = float(np.median(reference_k)) if reference_k else float(row.get("K_ME_v_per_a") or 0.0)
                row["K_ME_deviation_pct"] = float((float(row["K_ME_v_per_a"]) / max(ref_k, 1e-30) - 1) * 100)
    for row in runs:
        _set_engineering_state(row, cfg)


def _summary(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[float, float, str], list[dict[str, Any]]] = {}
    for row in runs:
        groups.setdefault((float(row["frequency_hz"]), float(row["awg_vpp"]), str(row.get("direction", "up"))), []).append(row)
    keys = ("voltage_peak_v", "voltage_pp_v", "voltage_rms_v", "voltage_fundamental_rms_v", "voltage_snr_db", "current_peak_a", "current_pp_a", "current_rms_a", "current_fundamental_rms_a", "voltage_thd_pct", "current_thd_pct", "receiver_peak_v", "receiver_pp_v", "receiver_rms_v", "receiver_fundamental_rms_v", "receiver_thd_pct", "receiver_snr_db", "K_ME_v_per_a", "K_ME_deviation_pct", "D_wave_pct", "correlation", "compression_db", "echo_peak_v", "echo_pp_v", "echo_rms_v", "echo_fundamental_rms_v", "echo_thd_pct", "echo_snr_db", "D_echo_pct", "echo_correlation")
    output = []
    for (frequency, vpp, direction), group in sorted(groups.items()):
        valid = [row for row in group if row.get("valid")]
        item: dict[str, Any] = {"frequency_hz": frequency, "awg_vpp": vpp, "direction": direction, "valid_count": len(valid), "total_count": len(group), "CV_pct": None,
                                "monitor_valid_count": sum(bool(row.get("monitor_valid")) for row in group),
                                "suspected_trip_count": sum(bool(row.get("suspected_trip")) for row in group),
                                "clipped_count": sum(bool(row.get("clipped")) for row in group),
                                "overflow_count": sum(bool(row.get("overflow")) for row in group),
                                "safety_warn_count": sum(row.get("safety_state") == "WARN" for row in group)}
        for key in keys:
            values = np.asarray([float(row[key]) for row in valid if row.get(key) is not None and math.isfinite(float(row[key]))], dtype=float)
            item[f"{key}_mean"] = float(np.mean(values)) if values.size else None
            item[f"{key}_std"] = float(np.std(values, ddof=1)) if values.size > 1 else (0.0 if values.size else None)
        amp = item.get("receiver_peak_v_mean")
        sd = item.get("receiver_peak_v_std")
        item["CV_pct"] = float(sd / amp * 100) if amp not in (None, 0) and sd is not None else None
        item["status"] = "有效" if valid else (group[0].get("status") or "无效")
        output.append(item)
    lookup = {(row["frequency_hz"], row["awg_vpp"], row["direction"]): row for row in output}
    for item in output:
        other_direction = "down" if item["direction"] == "up" else "up"
        other = lookup.get((item["frequency_hz"], item["awg_vpp"], other_direction))
        item["sweep_difference_receiver_pct"] = None
        item["sweep_difference_K_ME_pct"] = None
        item["sweep_difference_D_wave_pct"] = None
        item["sweep_difference_current_thd_pct"] = None
        item["sweep_difference_receiver_thd_pct"] = None
        item["sweep_difference_D_echo_pct"] = None
        item["sweep_difference_compression_db"] = None
        if not other:
            continue
        for output_key, metric_key in (
            ("sweep_difference_receiver_pct", "receiver_peak_v_mean"),
            ("sweep_difference_K_ME_pct", "K_ME_v_per_a_mean"),
            ("sweep_difference_current_thd_pct", "current_thd_pct_mean"),
            ("sweep_difference_receiver_thd_pct", "receiver_thd_pct_mean"),
            ("sweep_difference_D_echo_pct", "D_echo_pct_mean"),
            ("sweep_difference_compression_db", "compression_db_mean"),
        ):
            a, b = item.get(metric_key), other.get(metric_key)
            if a is not None and b is not None:
                item[output_key] = float(abs(a - b) / max((abs(a) + abs(b)) / 2, 1e-30) * 100) if output_key != "sweep_difference_compression_db" else float(abs(a - b))
        if item.get("D_wave_pct_mean") is not None and other.get("D_wave_pct_mean") is not None:
            item["sweep_difference_D_wave_pct"] = float(abs(item["D_wave_pct_mean"] - other["D_wave_pct_mean"]))
    return output


def _persist(folder: Path, cfg: LinearityConfig, saved_config: dict[str, Any], runs: list[dict[str, Any]], summary: list[dict[str, Any]]) -> None:
    _write_csv(folder / "linearity_runs.csv", runs)
    _write_csv(folder / "linearity_summary.csv", summary)
    # Keep the acquisition-time configuration immutable during offline reanalysis.
    if not (folder / "linearity_config.json").exists():
        (folder / "linearity_config.json").write_text(json.dumps(saved_config, ensure_ascii=False, indent=2), encoding="utf-8")


def execute_linearity_test(base_config: AcquisitionConfig, cfg: LinearityConfig, device: Pico4824A, output_root: Path, stop_event: threading.Event, progress: LinearityProgress | None = None, on_trip: LinearityTripHandler | None = None) -> dict[str, Any]:
    base_config.validate()
    cfg.validate()
    channels = (cfg.voltage_channel, cfg.current_channel, cfg.receiver_channel)
    if set(base_config.enabled_channels) != set(channels):
        raise ValueError("线性度测试必须且仅能同步启用 Voltage Monitor、Current Monitor、Receiver 三路所选通道")
    if cfg.sample_rate_hz / cfg.frequency_hz < 8:
        raise ValueError("每个激励周期至少需要 8 个采样点")
    stamp = datetime.now().strftime("linearity_%Y%m%d_%H%M%S_%f")
    folder = output_root / stamp
    raw_dir = folder / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    config_payload = {"timestamp": datetime.now().astimezone().isoformat(), "measurement": asdict(cfg), "base_config": base_config.to_dict(),
                      "definitions": {
                          "K_ME": "receiver_peak_v / current_peak_a",
                          "receiver_amplitude": "direct-wave absolute peak after noise-window DC removal",
                          "harmonics": (
                              "Monitor channels use a measured, trigger-independent central "
                              "flat-top window; receiver harmonics retain the user-selected "
                              "direct-wave window"
                          ),
                          "D_wave": "after bounded cross-correlation alignment and unconstrained least-squares amplitude scaling",
                          "monitor_valid": (
                              "Voltage and Current Monitor SNR must both meet the configured "
                              "minimum; suspected trip or ADC clipping/overflow invalidates the point"
                          ),
                          "suspected_trip": (
                              "sustained half-cycle RMS-envelope drop during the configured "
                              "flat burst, excluding the natural AWG fade; short or incomplete "
                              "bursts are explicitly unassessed"
                          ),
                      }}
    (folder / "linearity_config.json").write_text(json.dumps(config_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows: list[dict[str, Any]] = []
    plan = cfg.scan_plan
    total = len(plan) * cfg.repeats
    safety_tripped = False
    for point_index, (direction, vpp) in enumerate(plan, start=1):
        for repeat in range(1, cfg.repeats + 1):
            if stop_event.is_set():
                _apply_models(folder, rows, cfg)
                _persist(folder, cfg, config_payload, rows, _summary(rows))
                return {"directory": str(folder), "run_rows": rows, "summary_rows": _summary(rows), "stopped": True, "safety_tripped": safety_tripped}
            index = (point_index - 1) * cfg.repeats + repeat
            capture_config = AcquisitionConfig.from_dict(base_config.to_dict())
            capture_config.sample_rate_hz = cfg.sample_rate_hz
            capture_config.pre_trigger_samples = round(cfg.sample_rate_hz * cfg.capture_duration_us * 1e-6 * cfg.trigger_position_percent / 100)
            capture_config.post_trigger_samples = max(1, round(cfg.sample_rate_hz * cfg.capture_duration_us * 1e-6) - capture_config.pre_trigger_samples)
            capture_config.trigger.enabled = True
            capture_config.trigger.source = cfg.voltage_channel if cfg.trigger_signal == "voltage" else cfg.current_channel
            capture_config.trigger.threshold_v = cfg.trigger_level_v
            capture_config.awg.enabled = True
            capture_config.awg.waveform = "lcr_tone"
            capture_config.awg.frequency_hz = cfg.frequency_hz
            capture_config.awg.cycles = cfg.cycles
            capture_config.awg.pk_to_pk_v = vpp
            capture_config.awg.offset_v = 0.0
            capture_config.awg.trigger_source = "software"
            capture_config.awg.tone_ramp_cycles = cfg.ramp_cycles
            capture_config.validate()
            if progress:
                progress({"phase": "capturing", "run_index": index, "total_runs": total, "point_index": point_index,
                          "total_points": len(plan), "repeat": repeat, "repeats": cfg.repeats,
                          "frequency_hz": cfg.frequency_hz, "awg_vpp": vpp, "direction": direction}, None)
            capture = device.capture(capture_config)
            raw_name = f"p{point_index:04d}_{direction}_v{vpp:07.4f}_r{repeat:03d}.npz"
            raw_path = save_npz(capture, raw_dir / raw_name)
            run_spec = {"run_index": index, "frequency_hz": cfg.frequency_hz, "cycles": cfg.cycles,
                        "direction": direction, "awg_vpp": vpp, "repeat": repeat,
                        "raw_file": str(raw_path.relative_to(folder)), "timestamp": datetime.now().astimezone().isoformat()}
            row = _base_run(capture, cfg, run_spec)
            rows.append(row)
            _apply_models(folder, rows, cfg)
            summary = _summary(rows)
            _persist(folder, cfg, config_payload, rows, summary)
            if progress:
                progress({"phase": "saved", "run_index": index, "total_runs": total, "point_index": point_index,
                          "total_points": len(plan), "repeat": repeat, "repeats": cfg.repeats,
                          "frequency_hz": cfg.frequency_hz, "awg_vpp": vpp, "direction": direction}, capture)
            if row.get("suspected_trip"):
                safety_tripped = True
                # No web/operator callback means fail closed. With the UI
                # callback, the scan blocks here until Continue or Stop.
                continue_scan = bool(on_trip and on_trip(row)) and not stop_event.is_set()
                row["trip_action"] = "operator_continue" if continue_scan else "operator_stop"
                _apply_models(folder, rows, cfg)
                summary = _summary(rows)
                _persist(folder, cfg, config_payload, rows, summary)
                if not continue_scan:
                    return {"directory": str(folder), "run_rows": rows, "summary_rows": summary,
                            "stopped": True, "safety_tripped": True}
            if cfg.interval_s and index < total:
                stop_event.wait(cfg.interval_s)
    summary = _summary(rows)
    _persist(folder, cfg, config_payload, rows, summary)
    return {"directory": str(folder), "run_rows": rows, "summary_rows": summary,
            "stopped": False, "safety_tripped": safety_tripped}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for key, value in list(row.items()):
            if value in {"", None}:
                row[key] = None
                continue
            if value[:1] in ("[", "{"):
                try:
                    row[key] = json.loads(value)
                    continue
                except json.JSONDecodeError: pass
            try:
                num = float(value)
                row[key] = (int(num) if num.is_integer() else num) if math.isfinite(num) else None
            except (ValueError, TypeError):
                if value.lower() in {"true", "false"}: row[key] = value.lower() == "true"
    return rows


def _saved_lcr_row_is_valid(row: dict[str, Any], mode: str) -> bool:
    """Count usable saved/reanalyzed rows rather than treating every run as valid."""
    reanalysis_state = row.get("reanalysis_state")
    if reanalysis_state is not None and reanalysis_state != "OK":
        return False
    if row.get("overflow") or row.get("overflow_channels"):
        return False
    if mode == "big_lcr" and row.get("safety_state") not in {None, "OK", "WARN"}:
        return False
    if row.get("valid") is not None:
        return bool(row["valid"])
    if "impedance_magnitude_ohm" in row:
        try:
            return math.isfinite(float(row["impedance_magnitude_ohm"]))
        except (TypeError, ValueError):
            return False
    return reanalysis_state == "OK" if reanalysis_state is not None else True


def analyze_linearity_directory(directory: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    folder = directory.resolve()
    config_path = folder / "linearity_config.json"
    if not config_path.is_file():
        raise ValueError("所选目录不是线性度测试文件夹")
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    raw_config = dict(saved.get("measurement", {}))
    raw_config.update(overrides or {})
    cfg = LinearityConfig.from_dict(raw_config)
    raw_rows = _read_csv(folder / "linearity_runs.csv")
    if not raw_rows:
        raise ValueError("所选文件夹没有 linearity_runs.csv")
    if len(raw_rows) > 10_000:
        raise ValueError("单次最多分析 10000 条线性度采集")
    runs: list[dict[str, Any]] = []
    warnings = []
    for source in raw_rows:
        try:
            capture = load_npz(_raw_path(folder, source))
            # Override all analysis windows/thresholds, but retain run stimulus metadata.
            result = _base_run(capture, cfg, source, recalculate=True)
            runs.append(result)
        except (OSError, ValueError, KeyError, IndexError) as exc:
            source = dict(source)
            source.update({"valid": False, "status": "ERROR", "analysis_message": str(exc)})
            runs.append(source)
            warnings.append(f"运行 {source.get('run_index', '?')}：{exc}")
    _apply_models(folder, runs, cfg)
    return {"mode": "linearity", "directory": str(folder), "config": saved, "analysis_settings": asdict(cfg),
            "run_rows": runs, "summary_rows": _summary(runs), "valid_runs": sum(bool(r.get("valid")) for r in runs),
            "total_runs": len(runs), "warnings": warnings[:50], "reanalysis": bool(overrides)}


def linearity_run_preview(directory: Path, run_index: int, max_points: int = 5000,
                         settings: dict[str, Any] | None = None) -> dict[str, Any]:
    folder = directory.resolve()
    saved = json.loads((folder / "linearity_config.json").read_text(encoding="utf-8"))
    config_values = dict(saved["measurement"])
    config_values.update(settings or {})
    cfg = LinearityConfig.from_dict(config_values)
    rows = _read_csv(folder / "linearity_runs.csv")
    row = next((item for item in rows if int(item["run_index"]) == int(run_index)), None)
    if row is None:
        raise ValueError("找不到该运行序号")
    if settings:
        recalculated = analyze_linearity_directory(folder, settings)
        row = next((item for item in recalculated["run_rows"] if int(item["run_index"]) == int(run_index)), row)
    capture = load_npz(_raw_path(folder, row))
    stride = max(1, math.ceil(capture.samples / max_points))
    indices = np.arange(0, capture.samples, stride)
    ref_t = ref_y = None
    try:
        reference_file = row.get("reference_raw_file")
        if reference_file:
            ref_capture = load_npz(_raw_path(folder, {"raw_file": reference_file}))
            ref_t, ref_y = _wave_from_capture(ref_capture, cfg.receiver_channel, cfg.direct_start_us, cfg.direct_end_us, max_points)
    except (OSError, ValueError, KeyError):
        pass
    direct_mask = _window(capture.time_s, cfg.direct_start_us, cfg.direct_end_us)
    direct_indices = np.flatnonzero(direct_mask)[::stride]
    receiver = np.asarray(capture.volts[cfg.receiver_channel], dtype=np.float64)
    direct_time = capture.time_s[direct_indices]
    direct_values = receiver[direct_indices]
    if direct_values.size:
        direct_values = direct_values - float(np.mean(direct_values))
    shift_s = float(row.get("aligned_shift_us") or 0.0) * 1e-6
    return {"run": row, "time_s": capture.time_s[indices].tolist(),
            "channels": {cfg.voltage_channel: capture.volts[cfg.voltage_channel][indices].tolist(),
                         cfg.current_channel: capture.volts[cfg.current_channel][indices].tolist(),
                         cfg.receiver_channel: capture.volts[cfg.receiver_channel][indices].tolist()},
            "reference_time_s": ref_t.tolist() if ref_t is not None else [],
            "reference_receiver_v": ref_y.tolist() if ref_y is not None else [],
            "current_receiver_aligned_time_s": (direct_time - shift_s).tolist(),
            "current_receiver_aligned_v": direct_values.tolist(),
            "directory": str(folder)}


def load_lcr_folder(mode: str, directory: Path, reanalyze: bool = False, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read one of the three saved LCR folder formats; never touches hardware."""
    folder = directory.resolve()
    if mode == "linearity":
        if not (folder / "linearity_config.json").is_file():
            # Migrate support for the original two-monitor analysis of old
            # big_lcr_* folders instead of abandoning those saved measurements.
            from .big_signal_linearity import analyze_linearity_directory as analyze_legacy
            harmonic_order = int((settings or {}).get("harmonic_order", 5))
            return analyze_legacy(folder, harmonic_order)
        if reanalyze:
            return analyze_linearity_directory(folder, settings)
        saved = json.loads((folder / "linearity_config.json").read_text(encoding="utf-8"))
        runs = _read_csv(folder / "linearity_runs.csv")
        summary = _read_csv(folder / "linearity_summary.csv")
        if not runs:
            raise ValueError("所选线性度文件夹没有 linearity_runs.csv")
        return {"mode": "linearity", "directory": str(folder), "config": saved,
                "analysis_settings": saved.get("measurement", {}), "run_rows": runs,
                "summary_rows": summary, "valid_runs": sum(bool(row.get("valid")) for row in runs),
                "total_runs": len(runs), "warnings": [], "reanalysis": False}
    if mode not in {"big_lcr", "small_lcr"}:
        raise ValueError("未知 LCR 文件夹分析模式")
    prefix = "big_lcr" if mode == "big_lcr" else "lcr"
    cfg_path = folder / f"{prefix}_config.json"
    if not cfg_path.is_file() or not (folder / "runs.csv").is_file():
        raise ValueError(f"所选目录缺少 {prefix} 配置或 runs.csv")
    config = json.loads(cfg_path.read_text(encoding="utf-8"))
    summary = _read_csv(folder / "summary.csv")
    runs = _read_csv(folder / "runs.csv")
    if reanalyze:
        from .big_signal_lcr import BigSignalLcrConfig, analyze_big_signal_impedance
        from .lcr import LcrConfig, analyze_impedance, load_lcr_calibration
        base_config = AcquisitionConfig.from_dict(config["base_config"])
        if mode == "big_lcr":
            measurement = BigSignalLcrConfig.from_dict(config["big_lcr"])
            for row in runs:
                try:
                    capture = load_npz(_raw_path(folder, row))
                    if capture.overflow_channels:
                        row["reanalysis_state"] = "ADC_OVERFLOW"
                        continue
                    row.update(analyze_big_signal_impedance(capture, measurement, float(row["frequency_hz"])))
                    row["reanalysis_state"] = "OK"
                except (OSError, KeyError, ValueError) as exc:
                    row["reanalysis_state"] = "ERROR"
                    row["reanalysis_message"] = str(exc)
        else:
            measurement = LcrConfig.from_dict(config["lcr"])
            calibration = None
            if measurement.calibration_enabled and measurement.calibration_file:
                calibration = load_lcr_calibration(measurement.calibration_file, base_config, measurement)
            for row in runs:
                try:
                    capture = load_npz(_raw_path(folder, row))
                    if capture.overflow_channels:
                        row["reanalysis_state"] = "ADC_OVERFLOW"
                        continue
                    frequency = float(row["frequency_hz"])
                    factor = calibration.factor_at(frequency) if calibration else 1.0 + 0.0j
                    row.update(analyze_impedance(capture, measurement, frequency, factor))
                    row["reanalysis_state"] = "OK"
                except (OSError, KeyError, ValueError) as exc:
                    row["reanalysis_state"] = "ERROR"
                    row["reanalysis_message"] = str(exc)
            if calibration is not None:
                config["reanalysis_calibration"] = {"file": str(calibration.path), "standard_resistance_ohm": calibration.standard_resistance_ohm}
        # Derive the frequency/drive summaries in memory without writing to the
        # original run, summary, config, or raw files.
        group_key = "awg_drive_vpp" if mode == "big_lcr" else None
        grouped: dict[tuple[float, float | None], list[dict[str, Any]]] = {}
        for row in runs:
            if row.get("reanalysis_state") == "OK":
                grouped.setdefault((float(row["frequency_hz"]), float(row[group_key]) if group_key else None), []).append(row)
        summary = []
        for (frequency, drive), group in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1] or 0)):
            output = {"frequency_hz": frequency, "repeats": len(group)}
            if drive is not None: output["awg_drive_vpp"] = drive
            keys = set.intersection(*(set(row) for row in group))
            for key in keys:
                if key in {"run_index", "repeat", "npz_file"} or not all(isinstance(row.get(key), (float, int)) for row in group):
                    continue
                values = [float(row[key]) for row in group]
                output[key] = float(np.mean(values))
                output[f"{key}_std"] = (
                    float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
                )
            summary.append(output)
    return {"mode": mode, "directory": str(folder), "config": config,
            "run_rows": runs, "summary_rows": summary,
            "valid_runs": sum(_saved_lcr_row_is_valid(row, mode) for row in runs),
            "total_runs": len(runs),
            "raw_available": all(bool(row.get("npz_file")) for row in runs), "reanalysis": reanalyze}
