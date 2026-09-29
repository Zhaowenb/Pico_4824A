"""Capture export helpers."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import numpy as np

from .device import CaptureResult
from .config import AcquisitionConfig, CHANNEL_NAMES


def default_stem() -> str:
    return datetime.now().strftime("capture_%Y%m%d_%H%M%S")


def save_npz(result: CaptureResult, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "sample_interval_s": result.sample_interval_s,
        "requested_sample_rate_hz": result.requested_sample_rate_hz,
        "actual_sample_rate_hz": result.actual_sample_rate_hz,
        "overflow_channels": result.overflow_channels,
        "simulated": result.simulated,
        "config": result.config.to_dict(),
    }
    arrays = {"time_s": result.time_s, **{f"ch_{k}_v": v for k, v in result.volts.items()}}
    np.savez_compressed(target, metadata_json=json.dumps(metadata), **arrays)
    return target


def load_npz(path: str | Path) -> CaptureResult:
    """Load a capture written by :func:`save_npz` without enabling pickle."""
    source = Path(path)
    with np.load(source, allow_pickle=False) as archive:
        metadata = json.loads(str(archive["metadata_json"].item()))
        time_s = np.asarray(archive["time_s"], dtype=np.float64)
        volts = {
            key[3:-2]: np.asarray(archive[key], dtype=np.float64)
            for key in archive.files
            if key.startswith("ch_")
            and key.endswith("_v")
            and key[3:-2] in CHANNEL_NAMES
        }
    return CaptureResult(
        time_s=time_s,
        volts=volts,
        sample_interval_s=float(metadata["sample_interval_s"]),
        requested_sample_rate_hz=float(metadata["requested_sample_rate_hz"]),
        actual_sample_rate_hz=float(metadata["actual_sample_rate_hz"]),
        overflow_channels=tuple(metadata.get("overflow_channels", ())),
        config=AcquisitionConfig.from_dict(metadata["config"]),
        simulated=bool(metadata.get("simulated", False)),
    )


def save_csv(result: CaptureResult, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    names = tuple(result.volts)
    matrix = np.column_stack([result.time_s, *(result.volts[name] for name in names)])
    header = "time_s," + ",".join(f"ch_{name}_v" for name in names)
    np.savetxt(target, matrix, delimiter=",", header=header, comments="", fmt="%.10g")
    metadata_path = target.with_suffix(".json")
    metadata_path.write_text(
        json.dumps(
            {
                "sample_interval_s": result.sample_interval_s,
                "requested_sample_rate_hz": result.requested_sample_rate_hz,
                "actual_sample_rate_hz": result.actual_sample_rate_hz,
                "overflow_channels": result.overflow_channels,
                "simulated": result.simulated,
                "config": result.config.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target
