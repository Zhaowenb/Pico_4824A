"""Offline linearity and waveform-distortion analysis for big-signal LCR runs.

Only the two ATA monitor captures are used. Harmonics are simultaneously
least-squares fitted in the saved flat-top window, rather than taken from a
whole-burst FFT where the programmed ramps would create spectral leakage.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .storage import load_npz


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _raw_path(directory: Path, row: dict[str, Any]) -> Path:
    relative = Path(str(row.get("npz_file", "")))
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".npz":
        raise ValueError("原始波形路径不合法")
    target = (directory / relative).resolve()
    if not target.is_relative_to((directory / "raw").resolve()):
        raise ValueError("原始波形必须位于测量文件夹的 raw 子目录")
    return target


def _harmonic_metrics(
    time_s: np.ndarray, signal: np.ndarray, frequency_hz: float, order: int
) -> dict[str, Any]:
    stride = max(1, math.ceil(time_s.size / 50_000))
    t = np.asarray(time_s[::stride], dtype=np.float64)
    y = np.asarray(signal[::stride], dtype=np.float64)
    if t.size < max(50, 8 * order):
        raise ValueError("平顶区采样点不足以拟合所选谐波阶数")
    omega_t = 2 * np.pi * frequency_hz * t
    columns = [np.ones(t.size)]
    for harmonic in range(1, order + 1):
        columns.extend((np.cos(harmonic * omega_t), np.sin(harmonic * omega_t)))
    design = np.column_stack(columns)
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    harmonics_rms = [
        float(math.hypot(coefficients[2 * k - 1], coefficients[2 * k]) / math.sqrt(2))
        for k in range(1, order + 1)
    ]
    fundamental = harmonics_rms[0]
    if fundamental < 1e-12:
        raise ValueError("基波过小，无法计算谐波失真")
    harmonic_power = sum(value * value for value in harmonics_rms[1:])
    residual_rms = float(np.sqrt(np.mean(np.square(y - design @ coefficients))))
    ac = y - float(np.mean(y))
    total_rms = float(np.sqrt(np.mean(ac * ac)))
    return {
        "fundamental_rms": fundamental,
        "thd_pct": 100 * math.sqrt(harmonic_power) / fundamental,
        "thdn_estimate_pct": 100 * math.sqrt(harmonic_power + residual_rms**2) / fundamental,
        "h2_dbc": 20 * math.log10(harmonics_rms[1] / fundamental) if order >= 2 and harmonics_rms[1] > 0 else None,
        "h3_dbc": 20 * math.log10(harmonics_rms[2] / fundamental) if order >= 3 and harmonics_rms[2] > 0 else None,
        "crest_factor": float(np.max(np.abs(ac)) / total_rms) if total_rms > 0 else None,
        "harmonics_rms": harmonics_rms,
    }


def analyze_linearity_directory(directory: Path, harmonic_order: int = 5) -> dict[str, Any]:
    """Analyze each valid saved run and compare transfer gain at equal frequency."""
    if not 2 <= harmonic_order <= 15:
        raise ValueError("最高谐波阶数必须在 2 到 15 之间")
    folder = directory.resolve()
    config_path = folder / "big_lcr_config.json"
    runs_path = folder / "runs.csv"
    if not config_path.is_file() or not runs_path.is_file():
        raise ValueError("所选目录不是完整的大信号 LCR 测量文件夹")
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    config = saved["big_lcr"]
    with runs_path.open("r", encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    if len(source_rows) > 10_000:
        raise ValueError("单次最多分析 10000 条大信号测量记录")
    voltage_channel = config["voltage_channel"]
    current_channel = config["current_channel"]
    voltage_scale = float(config["voltage_monitor_scale_v_per_v"])
    current_scale = abs(float(config["current_monitor_scale_a_per_v"]))
    analyzed: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in source_rows:
        output: dict[str, Any] = {
            "run_index": int(row["run_index"]),
            "repeat": int(row["repeat"]),
            "frequency_hz": float(row["frequency_hz"]),
            "awg_drive_vpp": float(row["awg_drive_vpp"]),
            "safety_state": row.get("safety_state", ""),
            "npz_file": row.get("npz_file", ""),
        }
        if row.get("safety_state") not in {"OK", "WARN"}:
            output["analysis_state"] = "SKIPPED_SAFETY"
            analyzed.append(output)
            continue
        try:
            capture = load_npz(_raw_path(folder, row))
            if capture.overflow_channels:
                raise ValueError("原始采集有 ADC 溢出")
            frequency = output["frequency_hz"]
            max_order = math.floor(0.45 * capture.actual_sample_rate_hz / frequency)
            if harmonic_order > max_order:
                raise ValueError(f"采样率不足，当前频率最多能分析 {max_order} 阶谐波")
            start = _finite(row.get("analysis_start_s"))
            end = _finite(row.get("analysis_end_s"))
            if start is None or end is None or end <= start:
                raise ValueError("记录中缺少有效的平顶拟合时间窗")
            selected = (capture.time_s >= start) & (capture.time_s <= end)
            if int(np.count_nonzero(selected)) < 50:
                raise ValueError("保存的分析时间窗内采样不足")
            v = _harmonic_metrics(
                capture.time_s[selected], capture.volts[voltage_channel][selected] * voltage_scale,
                frequency, harmonic_order,
            )
            i = _harmonic_metrics(
                capture.time_s[selected], capture.volts[current_channel][selected] * current_scale,
                frequency, harmonic_order,
            )
            output.update({
                "analysis_state": "OK",
                "voltage_fundamental_rms_v": v["fundamental_rms"],
                "current_fundamental_rms_a": i["fundamental_rms"],
                "voltage_thd_pct": v["thd_pct"],
                "current_thd_pct": i["thd_pct"],
                "voltage_thdn_estimate_pct": v["thdn_estimate_pct"],
                "current_thdn_estimate_pct": i["thdn_estimate_pct"],
                "voltage_h2_dbc": v["h2_dbc"],
                "current_h2_dbc": i["h2_dbc"],
                "voltage_h3_dbc": v["h3_dbc"],
                "current_h3_dbc": i["h3_dbc"],
                "voltage_crest_factor": v["crest_factor"],
                "current_crest_factor": i["crest_factor"],
                "voltage_harmonics_rms_v": v["harmonics_rms"],
                "current_harmonics_rms_a": i["harmonics_rms"],
                "window_start_s": start,
                "window_end_s": end,
            })
        except (OSError, KeyError, ValueError, IndexError) as exc:
            output["analysis_state"] = "ERROR"
            output["analysis_message"] = str(exc)
            warnings.append(f"运行 {row['run_index']}：{exc}")
        analyzed.append(output)
    valid = [row for row in analyzed if row["analysis_state"] == "OK"]
    by_frequency: dict[float, list[dict[str, Any]]] = {}
    for row in valid:
        by_frequency.setdefault(row["frequency_hz"], []).append(row)
    for group in by_frequency.values():
        minimum_drive = min(row["awg_drive_vpp"] for row in group)
        baseline = [row for row in group if row["awg_drive_vpp"] == minimum_drive]
        for signal in ("voltage", "current"):
            key = f"{signal}_fundamental_rms_{'v' if signal == 'voltage' else 'a'}"
            reference = float(np.mean([row[key] / minimum_drive for row in baseline]))
            for row in group:
                gain = row[key] / row["awg_drive_vpp"]
                row[f"{signal}_transfer_gain"] = gain
                deviation = 20 * math.log10(gain / reference) if reference > 0 else None
                row[f"{signal}_gain_deviation_db"] = (
                    0.0 if deviation is not None and abs(deviation) < 1e-9 else deviation
                )
    grouped: dict[tuple[float, float], list[dict[str, Any]]] = {}
    for row in valid:
        grouped.setdefault((row["frequency_hz"], row["awg_drive_vpp"]), []).append(row)
    summary = []
    metric_keys = (
        "voltage_fundamental_rms_v", "current_fundamental_rms_a",
        "voltage_thd_pct", "current_thd_pct",
        "voltage_thdn_estimate_pct", "current_thdn_estimate_pct",
        "voltage_h2_dbc", "current_h2_dbc", "voltage_h3_dbc", "current_h3_dbc",
        "voltage_gain_deviation_db", "current_gain_deviation_db",
    )
    for (frequency, drive), group in sorted(grouped.items()):
        item: dict[str, Any] = {"frequency_hz": frequency, "awg_drive_vpp": drive, "repeats": len(group)}
        for key in metric_keys:
            values = [row[key] for row in group if row.get(key) is not None]
            item[key] = float(np.mean(values)) if values else None
        summary.append(item)
    return {
        "directory": str(folder), "harmonic_order": harmonic_order,
        "run_rows": analyzed, "summary_rows": summary,
        "valid_runs": len(valid), "total_runs": len(source_rows),
        "warnings": warnings[:30],
    }


def linearity_run_preview(directory: Path, run_index: int, max_points: int = 4000) -> dict[str, Any]:
    folder = directory.resolve()
    config = json.loads((folder / "big_lcr_config.json").read_text(encoding="utf-8"))["big_lcr"]
    with (folder / "runs.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        row = next((item for item in csv.DictReader(stream) if int(item["run_index"]) == run_index), None)
    if row is None:
        raise ValueError("找不到该运行序号")
    capture = load_npz(_raw_path(folder, row))
    start = _finite(row.get("analysis_start_s"))
    end = _finite(row.get("analysis_end_s"))
    first = max(0, int(np.searchsorted(capture.time_s, start - 2 / float(row["frequency_hz"])))) if start is not None else 0
    last = min(capture.time_s.size, int(np.searchsorted(capture.time_s, end + 2 / float(row["frequency_hz"])))) if end is not None else capture.time_s.size
    step = max(1, math.ceil((last - first) / max_points))
    indices = slice(first, last, step)
    return {
        "run_index": run_index,
        "time_s": capture.time_s[indices].tolist(),
        "voltage_v": (capture.volts[config["voltage_channel"]][indices] * float(config["voltage_monitor_scale_v_per_v"])).tolist(),
        "current_a": (capture.volts[config["current_channel"]][indices] * float(config["current_monitor_scale_a_per_v"]) * int(config["current_monitor_polarity"])).tolist(),
        "window_start_s": start, "window_end_s": end,
    }
