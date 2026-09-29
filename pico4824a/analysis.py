"""Non-destructive waveform and multi-window spectrum analysis."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .config import CHANNEL_NAMES
from .storage import load_npz
from .sweep import SweepConfig, aggregate_rows, capture_metrics


SUPPORTED_SUFFIXES = {".npz", ".csv"}


def _typed_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    converted: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for key, value in row.items():
            text = "" if value is None else value.strip()
            if text == "":
                item[key] = ""
                continue
            try:
                number = float(text)
                item[key] = int(number) if number.is_integer() and "frequency" not in key else number
            except ValueError:
                item[key] = text
        converted.append(item)
    return converted


def load_sweep_directory(path: str | Path) -> dict[str, Any]:
    """Load one durable sweep archive as a parameter experiment."""
    directory = Path(path).expanduser().resolve()
    if directory.is_file():
        directory = directory.parent
    if directory.name.lower() == "raw":
        directory = directory.parent
    config_path = directory / "sweep_config.json"
    runs_path = directory / "runs.csv"
    summary_path = directory / "summary.csv"
    if (
        directory.is_dir()
        and not (config_path.is_file() and runs_path.is_file() and summary_path.is_file())
    ):
        candidates = sorted(
            (
                item
                for item in directory.iterdir()
                if item.is_dir()
                and (item / "sweep_config.json").is_file()
                and (item / "runs.csv").is_file()
                and (item / "summary.csv").is_file()
            ),
            key=lambda item: item.name,
            reverse=True,
        )
        if candidates:
            directory = candidates[0].resolve()
            config_path = directory / "sweep_config.json"
            runs_path = directory / "runs.csv"
            summary_path = directory / "summary.csv"
    if not (config_path.is_file() and runs_path.is_file() and summary_path.is_file()):
        return {"is_sweep": False, "directory": str(directory)}
    config = json.loads(config_path.read_text(encoding="utf-8"))
    runs = _typed_csv_rows(runs_path)
    summary = _typed_csv_rows(summary_path)
    root = directory.resolve()
    for row in runs:
        relative = str(row.get("npz_file", ""))
        candidate = (root / relative).resolve()
        row["absolute_npz_file"] = (
            str(candidate) if root in candidate.parents and candidate.is_file() else ""
        )
    frequencies = sorted({float(row["frequency_hz"]) for row in summary})
    cycles = sorted({int(row["cycles"]) for row in summary})
    preferred_metrics = [
        "tail_to_direct_db",
        "reflection_to_direct_db",
        "quality_db",
        "receiver_tail_rms_v",
        "tail_to_direct_db_std",
        "reflection_to_direct_db_std",
        "quality_db_std",
        "receiver_tail_rms_v_std",
        "reflection_snr_db",
        "strength_db_from_max",
        "settling_to_threshold_us",
        "nominal_duration_us",
    ]
    available_metrics = [
        key
        for key in preferred_metrics
        if summary and key in summary[0] and isinstance(summary[0][key], (int, float))
    ]
    return {
        "is_sweep": True,
        "directory": str(directory),
        "config": config,
        "runs": runs,
        "summary": summary,
        "frequencies_hz": frequencies,
        "cycles": cycles,
        "metrics": available_metrics,
        "points": len(summary),
        "runs_completed": len(runs),
    }


def reevaluate_sweep_directory(
    path: str | Path, evaluation: dict[str, Any]
) -> dict[str, Any]:
    """Recompute every saved run without modifying the original sweep archive."""
    archive = load_sweep_directory(path)
    if not archive.get("is_sweep"):
        raise ValueError("所选目录不是完整的参数扫描归档")
    saved_sweep = dict(archive.get("config", {}).get("sweep", {}))
    saved_sweep.update(evaluation)
    sweep = SweepConfig.from_dict(saved_sweep)
    recomputed_runs: list[dict[str, Any]] = []
    for saved_row in archive["runs"]:
        npz_path = str(saved_row.get("absolute_npz_file", ""))
        if not npz_path:
            raise ValueError(
                f"第 {saved_row.get('run_index', '?')} 次采集缺少原始NPZ，不能重新评价"
            )
        result = load_npz(npz_path)
        row = {
            "run_index": int(saved_row["run_index"]),
            "point_index": int(saved_row["point_index"]),
            "repeat": int(saved_row["repeat"]),
            "frequency_hz": float(saved_row["frequency_hz"]),
            "cycles": int(saved_row["cycles"]),
            "npz_file": str(saved_row.get("npz_file", "")),
            "absolute_npz_file": npz_path,
            "receiver_channels": "/".join(sweep.receiver_channels),
            **capture_metrics(result, sweep),
        }
        recomputed_runs.append(row)
    summary = aggregate_rows(recomputed_runs, sweep)
    archive["runs"] = recomputed_runs
    archive["summary"] = summary
    archive["config"] = {
        **archive["config"],
        "sweep": {
            **saved_sweep,
            "receiver_channels": list(sweep.receiver_channels),
            "transmitter_channel": sweep.transmitter_channel,
        },
    }
    preferred_metrics = [
        "tail_to_direct_db",
        "reflection_to_direct_db",
        "quality_db",
        "receiver_tail_rms_v",
        "tail_to_direct_db_std",
        "reflection_to_direct_db_std",
        "quality_db_std",
        "receiver_tail_rms_v_std",
        "reflection_snr_db",
        "strength_db_from_max",
        "settling_to_threshold_us",
        "nominal_duration_us",
    ]
    archive["metrics"] = [
        key for key in preferred_metrics
        if summary and key in summary[0] and isinstance(summary[0][key], (int, float))
    ]
    archive["reevaluated"] = True
    return archive


def discover_sources(path: str | Path, limit: int = 2000) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"路径不存在：{source}")
    if source.is_file():
        if source.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError("只支持 NPZ 和 CSV 数据文件")
        return {"kind": "file", "root": str(source.parent), "files": [str(source)]}
    files: list[Path] = []
    files.extend(source.rglob("*.npz"))
    for item in source.rglob("*.csv"):
        try:
            with item.open("r", encoding="utf-8-sig", newline="") as stream:
                first = next(csv.reader(stream), [])
            if first and first[0].strip() == "time_s":
                files.append(item)
        except (OSError, UnicodeError):
            continue
    # If both formats exist for the same stem, prefer NPZ because it retains
    # acquisition metadata and avoids parsing a large text copy.
    npz_stems = {item.with_suffix("").resolve() for item in files if item.suffix.lower() == ".npz"}
    unique = [
        item.resolve()
        for item in files
        if item.suffix.lower() == ".npz" or item.with_suffix("").resolve() not in npz_stems
    ]
    unique = sorted(set(unique), key=lambda item: str(item).lower())
    truncated = len(unique) > limit
    unique = unique[:limit]
    return {
        "kind": "folder",
        "root": str(source),
        "files": [str(item) for item in unique],
        "truncated": truncated,
        "count": len(unique),
    }


def _metadata_text(value: np.ndarray) -> dict[str, Any]:
    try:
        return json.loads(str(value.item()))
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


def load_dataset(path: str | Path) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"数据文件不存在：{source}")
    if source.suffix.lower() == ".npz":
        with np.load(source, allow_pickle=False) as archive:
            if "time_s" not in archive.files:
                raise ValueError("NPZ 中缺少 time_s")
            time_s = np.asarray(archive["time_s"], dtype=np.float64)
            channels = {
                key[3:-2]: np.asarray(archive[key], dtype=np.float64)
                for key in archive.files
                if key.startswith("ch_")
                and key.endswith("_v")
                and key[3:-2] in CHANNEL_NAMES
            }
            metadata = (
                _metadata_text(archive["metadata_json"])
                if "metadata_json" in archive.files
                else {}
            )
    elif source.suffix.lower() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            header = next(csv.reader(stream), [])
        if not header or header[0].strip() != "time_s":
            raise ValueError("CSV 第一列必须是 time_s")
        matrix = np.loadtxt(source, delimiter=",", skiprows=1, ndmin=2)
        if matrix.shape[1] != len(header):
            raise ValueError("CSV 表头与数据列数不一致")
        time_s = np.asarray(matrix[:, 0], dtype=np.float64)
        channels = {}
        for index, name in enumerate(header[1:], start=1):
            clean = name.strip()
            if (
                clean.startswith("ch_")
                and clean.endswith("_v")
                and clean[3:-2] in CHANNEL_NAMES
            ):
                channels[clean[3:-2]] = np.asarray(matrix[:, index], dtype=np.float64)
        sidecar = source.with_suffix(".json")
        metadata = (
            json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
        )
    else:
        raise ValueError("只支持 NPZ 和 CSV 数据文件")

    if time_s.ndim != 1 or time_s.size < 4:
        raise ValueError("数据至少需要4个时间采样点")
    if not channels:
        raise ValueError("文件中没有 ch_A_v 这类通道列")
    if not np.all(np.isfinite(time_s)) or np.any(np.diff(time_s) <= 0):
        raise ValueError("time_s 必须有限且严格递增")
    for name, values in channels.items():
        if values.shape != time_s.shape:
            raise ValueError(f"通道 {name} 与 time_s 长度不一致")
    sample_rate_hz = 1.0 / float(np.median(np.diff(time_s)))
    metadata = dict(metadata)
    metadata.update(
        {
            "source": str(source),
            "samples": int(time_s.size),
            "sample_rate_hz": sample_rate_hz,
            "time_start_s": float(time_s[0]),
            "time_end_s": float(time_s[-1]),
            "channels": sorted(channels),
        }
    )
    return time_s, channels, metadata


def fft_bandpass(
    values: np.ndarray,
    sample_rate_hz: float,
    low_hz: float,
    high_hz: float,
    transition_hz: float,
) -> np.ndarray:
    """Zero-phase FFT bandpass with reflection padding and cosine transitions."""
    nyquist = sample_rate_hz / 2.0
    if not 0.0 <= low_hz < high_hz < nyquist:
        raise ValueError(f"带通范围必须满足 0 ≤ 下限 < 上限 < {nyquist:g} Hz")
    if transition_hz < 0:
        raise ValueError("过渡带宽不能为负")
    centered = np.asarray(values, dtype=np.float64) - float(np.median(values))
    pad = min(max(128, centered.size // 20), max(0, centered.size - 2))
    padded = np.pad(centered, pad, mode="reflect") if pad else centered
    frequencies = np.fft.rfftfreq(padded.size, 1.0 / sample_rate_hz)
    weights = np.zeros_like(frequencies)
    weights[(frequencies >= low_hz) & (frequencies <= high_hz)] = 1.0
    width = min(transition_hz, max(high_hz - low_hz, 0.0))
    if width > 0:
        lower_start = max(0.0, low_hz - width)
        lower = (frequencies >= lower_start) & (frequencies < low_hz)
        if low_hz > lower_start:
            weights[lower] = 0.5 - 0.5 * np.cos(
                np.pi * (frequencies[lower] - lower_start) / (low_hz - lower_start)
            )
        upper_end = min(nyquist, high_hz + width)
        upper = (frequencies > high_hz) & (frequencies <= upper_end)
        if upper_end > high_hz:
            weights[upper] = 0.5 + 0.5 * np.cos(
                np.pi * (frequencies[upper] - high_hz) / (upper_end - high_hz)
            )
    filtered = np.fft.irfft(np.fft.rfft(padded) * weights, n=padded.size)
    return filtered[pad:-pad] if pad else filtered


def _window_values(name: str, samples: int) -> np.ndarray:
    if name == "hann":
        return np.hanning(samples)
    if name == "blackman":
        return np.blackman(samples)
    if name == "rectangular":
        return np.ones(samples, dtype=np.float64)
    raise ValueError("频谱窗函数必须是 hann、blackman 或 rectangular")


def _spectrum(
    values: np.ndarray,
    sample_rate_hz: float,
    window_name: str,
    mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    centered = np.asarray(values, dtype=np.float64) - float(np.mean(values))
    window = _window_values(window_name, centered.size)
    transformed = np.fft.rfft(centered * window)
    frequencies = np.fft.rfftfreq(centered.size, 1.0 / sample_rate_hz)
    if mode == "amplitude":
        scale = max(float(np.sum(window)), 1e-30)
        result = np.abs(transformed) / scale
        if result.size > 2:
            result[1:-1] *= 2.0
    elif mode == "psd":
        scale = max(sample_rate_hz * float(np.sum(window**2)), 1e-30)
        result = np.abs(transformed) ** 2 / scale
        if result.size > 2:
            result[1:-1] *= 2.0
    else:
        raise ValueError("频谱类型必须是 amplitude 或 psd")
    return frequencies, result


def _downsample_indices(size: int, maximum: int) -> np.ndarray:
    step = max(1, int(math.ceil(size / maximum)))
    return np.arange(0, size, step, dtype=np.int64)


def process_dataset(path: str | Path, raw: dict[str, Any]) -> dict[str, Any]:
    time_s, all_channels, metadata = load_dataset(path)
    sample_rate_hz = float(metadata["sample_rate_hz"])
    requested_raw = raw.get("channels")
    requested = [
        str(name).upper()
        for name in (requested_raw if requested_raw else all_channels)
    ]
    names = [name for name in requested if name in all_channels]
    if not names:
        raise ValueError("至少选择一个存在的数据通道")

    filter_raw = raw.get("filter", {})
    filter_enabled = bool(filter_raw.get("enabled", False))
    low_hz = float(filter_raw.get("low_hz", 20_000.0))
    high_hz = float(filter_raw.get("high_hz", 180_000.0))
    transition_hz = float(filter_raw.get("transition_hz", 5_000.0))
    filtered = {
        name: (
            fft_bandpass(all_channels[name], sample_rate_hz, low_hz, high_hz, transition_hz)
            if filter_enabled
            else np.asarray(all_channels[name], dtype=np.float64).copy()
        )
        for name in names
    }

    time_start_us = float(raw.get("time_start_us", time_s[0] * 1e6))
    time_end_us = float(raw.get("time_end_us", time_s[-1] * 1e6))
    if time_end_us <= time_start_us:
        raise ValueError("显示时间终点必须晚于起点")
    visible = (time_s * 1e6 >= time_start_us) & (time_s * 1e6 <= time_end_us)
    visible_indices = np.flatnonzero(visible)
    if visible_indices.size < 2:
        raise ValueError("显示时间范围内没有足够的数据点")
    selected = visible_indices[_downsample_indices(visible_indices.size, 6000)]
    show_raw = bool(raw.get("show_raw", True))
    show_filtered = bool(raw.get("show_filtered", filter_enabled))
    waveform: dict[str, list[float]] = {}
    for name in names:
        if show_raw:
            waveform[f"{name}:raw"] = all_channels[name][selected].tolist()
        if show_filtered:
            waveform[f"{name}:filtered"] = filtered[name][selected].tolist()

    spectrum_raw = raw.get("spectrum", {})
    spectrum_source = str(spectrum_raw.get("source", "filtered"))
    spectrum_values = filtered if spectrum_source == "filtered" else all_channels
    window_function = str(spectrum_raw.get("window_function", "hann"))
    spectrum_mode = str(spectrum_raw.get("mode", "amplitude"))
    frequency_min_hz = max(0.0, float(spectrum_raw.get("frequency_min_hz", 0.0)))
    frequency_max_hz = min(
        sample_rate_hz / 2.0,
        float(spectrum_raw.get("frequency_max_hz", sample_rate_hz / 2.0)),
    )
    if frequency_max_hz <= frequency_min_hz:
        raise ValueError("频谱频率终点必须大于起点")
    windows = list(spectrum_raw.get("windows", []))[:8]
    if not windows:
        windows = [
            {
                "label": "当前显示区间",
                "start_us": time_start_us,
                "end_us": time_end_us,
                "color": "#35d0ba",
            }
        ]
    spectra: list[dict[str, Any]] = []
    time_us = time_s * 1e6
    for index, item in enumerate(windows):
        start_us = float(item.get("start_us", time_start_us))
        end_us = float(item.get("end_us", time_end_us))
        if end_us <= start_us:
            raise ValueError(f"频谱时间窗 {index + 1} 的终点必须晚于起点")
        mask = (time_us >= start_us) & (time_us <= end_us)
        if np.count_nonzero(mask) < 16:
            raise ValueError(f"频谱时间窗 {index + 1} 至少需要16个采样点")
        entry: dict[str, Any] = {
            "label": str(item.get("label", f"时段 {index + 1}")),
            "start_us": start_us,
            "end_us": end_us,
            "color": str(item.get("color", "#35d0ba")),
            "channels": {},
        }
        frequency_indices: np.ndarray | None = None
        for name in names:
            frequencies, values = _spectrum(
                np.asarray(spectrum_values[name])[mask],
                sample_rate_hz,
                window_function,
                spectrum_mode,
            )
            allowed = np.flatnonzero(
                (frequencies >= frequency_min_hz) & (frequencies <= frequency_max_hz)
            )
            if allowed.size == 0:
                raise ValueError("所选频率范围内没有频谱点")
            frequency_indices = allowed[_downsample_indices(allowed.size, 4000)]
            entry["channels"][name] = values[frequency_indices].tolist()
        assert frequency_indices is not None
        entry["frequency_hz"] = frequencies[frequency_indices].tolist()
        spectra.append(entry)

    return {
        "metadata": metadata,
        "time_s": time_s[selected].tolist(),
        "waveform": waveform,
        "spectra": spectra,
        "filter": {
            "enabled": filter_enabled,
            "low_hz": low_hz,
            "high_hz": high_hz,
            "transition_hz": transition_hz,
        },
    }


def export_filtered(
    path: str | Path,
    raw: dict[str, Any],
    output_format: str,
) -> Path:
    source = Path(path).expanduser().resolve()
    time_s, channels, metadata = load_dataset(source)
    sample_rate_hz = float(metadata["sample_rate_hz"])
    low_hz = float(raw.get("low_hz", 20_000.0))
    high_hz = float(raw.get("high_hz", 180_000.0))
    transition_hz = float(raw.get("transition_hz", 5_000.0))
    filtered = {
        name: fft_bandpass(values, sample_rate_hz, low_hz, high_hz, transition_hz)
        for name, values in channels.items()
    }
    output_directory = source.parent / "processed"
    output_directory.mkdir(parents=True, exist_ok=True)
    band_label = f"{low_hz / 1000:g}-{high_hz / 1000:g}kHz"
    target = output_directory / f"{source.stem}_bandpass_{band_label}.{output_format}"
    processing = {
        "source": str(source),
        "method": "zero_phase_fft_bandpass",
        "low_hz": low_hz,
        "high_hz": high_hz,
        "transition_hz": transition_hz,
        "raw_modified": False,
    }
    if output_format == "npz":
        arrays = {"time_s": time_s, **{f"ch_{name}_v": values for name, values in filtered.items()}}
        metadata_out = dict(metadata)
        metadata_out["processing"] = processing
        np.savez_compressed(
            target,
            metadata_json=json.dumps(metadata_out, ensure_ascii=False),
            **arrays,
        )
    elif output_format == "csv":
        names = sorted(filtered)
        matrix = np.column_stack([time_s, *(filtered[name] for name in names)])
        np.savetxt(
            target,
            matrix,
            delimiter=",",
            header="time_s," + ",".join(f"ch_{name}_v" for name in names),
            comments="",
            fmt="%.10g",
        )
        target.with_suffix(".json").write_text(
            json.dumps({"processing": processing, **metadata}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        raise ValueError("导出格式必须是 npz 或 csv")
    return target
