"""Local web control server for PicoScope 4824A.

The server intentionally binds to localhost by default. PicoSDK and the USB
device remain on the instrument computer; the browser is only the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

from .config import AcquisitionConfig, AwgConfig
from .analysis import (
    discover_sources,
    export_filtered,
    fft_bandpass,
    load_dataset,
    load_sweep_directory,
    process_dataset,
    reevaluate_sweep_directory,
)
from .device import CaptureResult, Pico4824A
from .experimental_modes import experimental_mode_analysis
from .lcr import (
    LcrConfig,
    LcrOutcome,
    discover_lcr_calibrations,
    execute_lcr,
)
from .big_signal_lcr import (
    BigSignalLcrConfig,
    BigSignalLcrOutcome,
    execute_big_signal_lcr,
)
from .big_signal_linearity import analyze_linearity_directory, linearity_run_preview
from .storage import default_stem, save_csv, save_npz
from .sweep import SweepConfig, SweepOutcome, execute_sweep
from .waveforms import normalized_waveform
from .time_frequency import time_frequency_map


PROJECT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_DIR / "web"


@dataclass(slots=True)
class WebStatus:
    state: str = "idle"
    message: str = "系统就绪"
    started_at: float | None = None
    finished_at: float | None = None
    capture_id: int = 0
    task_kind: str = "capture"
    progress: dict[str, Any] | None = None


class WebControlState:
    def __init__(
        self,
        sweep_output_root: Path | None = None,
        lcr_output_root: Path | None = None,
        big_lcr_output_root: Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.status = WebStatus()
        self.result: CaptureResult | None = None
        self.sweep_result: SweepOutcome | None = None
        self.lcr_result: LcrOutcome | None = None
        self.big_lcr_result: BigSignalLcrOutcome | None = None
        self.device: Pico4824A | None = None
        self._hardware_device: Pico4824A | None = None
        self._worker_thread: threading.Thread | None = None
        self.sweep_output_root = sweep_output_root or PROJECT_DIR / "data" / "sweeps"
        self.lcr_output_root = lcr_output_root or PROJECT_DIR / "data" / "lcr"
        self.big_lcr_output_root = big_lcr_output_root or PROJECT_DIR / "data" / "lcr_big"
        self._stop_event = threading.Event()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.status.state,
                "message": self.status.message,
                "started_at": self.status.started_at,
                "finished_at": self.status.finished_at,
                "capture_id": self.status.capture_id,
                "task_kind": self.status.task_kind,
                "progress": self.status.progress,
                "has_result": self.result is not None,
                "has_sweep_result": self.sweep_result is not None,
                "has_lcr_result": self.lcr_result is not None,
                "has_big_lcr_result": self.big_lcr_result is not None,
            }

    def start_capture(self, raw: dict[str, Any]) -> int:
        values = dict(raw)
        simulate = bool(values.pop("simulate", False))
        config = AcquisitionConfig.from_dict(values)
        with self._lock:
            if self.status.state == "running":
                raise RuntimeError("已有采集任务正在运行")
            self._stop_event.clear()
            self.status.capture_id += 1
            capture_id = self.status.capture_id
            self.status.state = "running"
            self.status.task_kind = "capture"
            self.status.progress = None
            self.status.message = "正在配置设备并等待触发"
            self.status.started_at = time.time()
            self.status.finished_at = None
        worker = threading.Thread(
            target=self._capture_worker,
            args=(capture_id, config, simulate),
            daemon=True,
            name=f"pico-capture-{capture_id}",
        )
        with self._lock:
            self._worker_thread = worker
        worker.start()
        return capture_id

    def start_sweep(self, raw: dict[str, Any]) -> int:
        config_raw = raw.get("config")
        sweep_raw = raw.get("sweep")
        if not isinstance(config_raw, dict) or not isinstance(sweep_raw, dict):
            raise ValueError("扫描请求必须同时包含 config 和 sweep")
        config_values = dict(config_raw)
        simulate = bool(config_values.pop("simulate", False))
        config = AcquisitionConfig.from_dict(config_values)
        sweep = SweepConfig.from_dict(sweep_raw)
        # Resolve the actual axes now so invalid/costly requests fail before the thread starts.
        points = sweep.points(config)
        if len(points) * sweep.repeats > 10_000:
            raise ValueError("一次扫描最多允许 10,000 次采集")
        with self._lock:
            if self.status.state == "running":
                raise RuntimeError("已有采集或扫描任务正在运行")
            self._stop_event.clear()
            self.status.capture_id += 1
            task_id = self.status.capture_id
            self.status.state = "running"
            self.status.task_kind = "sweep"
            self.status.message = (
                f"扫描已启动：{len(points)} 个参数点，共 {len(points) * sweep.repeats} 次采集"
            )
            self.status.started_at = time.time()
            self.status.finished_at = None
            self.status.progress = {
                "phase": "starting",
                "run_index": 0,
                "total_runs": len(points) * sweep.repeats,
                "point_index": 0,
                "total_points": len(points),
            }
            self.sweep_result = None
        worker = threading.Thread(
            target=self._sweep_worker,
            args=(task_id, config, sweep, simulate),
            daemon=True,
            name=f"pico-sweep-{task_id}",
        )
        with self._lock:
            self._worker_thread = worker
        worker.start()
        return task_id

    def start_lcr(self, raw: dict[str, Any]) -> int:
        return self._start_lcr_task(raw, calibration_standard_ohm=None)

    def start_lcr_calibration(self, raw: dict[str, Any]) -> int:
        standard = float(raw.get("standard_resistance_ohm", 0.0))
        if not np.isfinite(standard) or standard <= 0:
            raise ValueError("精准电阻阻值必须是大于0的有限数")
        return self._start_lcr_task(raw, calibration_standard_ohm=standard)

    def _start_lcr_task(
        self,
        raw: dict[str, Any],
        calibration_standard_ohm: float | None,
    ) -> int:
        config_raw = raw.get("config")
        lcr_raw = raw.get("lcr")
        if not isinstance(config_raw, dict) or not isinstance(lcr_raw, dict):
            raise ValueError("LCR 请求必须同时包含 config 和 lcr")
        config_values = dict(config_raw)
        simulate = bool(config_values.pop("simulate", False))
        config = AcquisitionConfig.from_dict(config_values)
        lcr = LcrConfig.from_dict(lcr_raw)
        with self._lock:
            if self.status.state == "running":
                raise RuntimeError("已有仪器任务正在运行")
            self._stop_event.clear()
            self.status.capture_id += 1
            task_id = self.status.capture_id
            self.status.state = "running"
            calibration_run = calibration_standard_ohm is not None
            self.status.task_kind = "lcr_calibration" if calibration_run else "lcr"
            prefix = (
                f"精准电阻校准 {calibration_standard_ohm:g} Ω"
                if calibration_run
                else "LCR"
            )
            self.status.message = (
                f"{prefix} 已启动：{len(lcr.frequencies_hz)} 个频点，"
                f"共 {len(lcr.frequencies_hz) * lcr.repeats} 次采集"
            )
            self.status.started_at = time.time()
            self.status.finished_at = None
            self.status.progress = {
                "phase": "starting",
                "run_index": 0,
                "total_runs": len(lcr.frequencies_hz) * lcr.repeats,
                "point_index": 0,
                "total_points": len(lcr.frequencies_hz),
            }
            self.lcr_result = None
        worker = threading.Thread(
            target=self._lcr_worker,
            args=(task_id, config, lcr, simulate, calibration_standard_ohm),
            daemon=True,
            name=f"pico-lcr-{task_id}",
        )
        with self._lock:
            self._worker_thread = worker
        worker.start()
        return task_id

    def start_big_lcr(self, raw: dict[str, Any]) -> int:
        """Start the independent ATA-2021B large-signal LCR task."""
        config_raw = raw.get("config")
        big_raw = raw.get("big_lcr")
        if not isinstance(config_raw, dict) or not isinstance(big_raw, dict):
            raise ValueError("大信号 LCR 请求必须同时包含 config 和 big_lcr")
        if raw.get("safety_acknowledged") is not True:
            raise ValueError("开始大信号测量前必须确认 ATA-2021B 安全参数")
        config_values = dict(config_raw)
        simulate = bool(config_values.pop("simulate", False))
        config = AcquisitionConfig.from_dict(config_values)
        big_lcr = BigSignalLcrConfig.from_dict(big_raw)
        total_points = len(big_lcr.parameter_points)
        total_runs = total_points * big_lcr.repeats
        with self._lock:
            if self.status.state == "running":
                raise RuntimeError("已有仪器任务正在运行")
            self._stop_event.clear()
            self.status.capture_id += 1
            task_id = self.status.capture_id
            self.status.state = "running"
            self.status.task_kind = "big_lcr"
            self.status.message = (
                f"ATA-2021B 大信号 LCR 已启动：{total_points} 个频率/Vpp 组合点，"
                f"共 {total_runs} 次采集"
            )
            self.status.started_at = time.time()
            self.status.finished_at = None
            self.status.progress = {
                "phase": "starting",
                "run_index": 0,
                "total_runs": total_runs,
                "point_index": 0,
                "total_points": total_points,
            }
            self.big_lcr_result = None
        worker = threading.Thread(
            target=self._big_lcr_worker,
            args=(task_id, config, big_lcr, simulate),
            daemon=True,
            name=f"pico-big-lcr-{task_id}",
        )
        with self._lock:
            self._worker_thread = worker
        worker.start()
        return task_id

    def _device_for_task(self, simulate: bool) -> tuple[Pico4824A, bool]:
        """Return a task device and whether it is temporary.

        Hardware stays open for the lifetime of the Web server. Reopening the
        USB unit for every click can reinitialize the AWG output and create a
        transient at a connected power amplifier. Simulator instances remain
        short-lived so hardware and simulation state cannot be mixed.
        """
        if simulate:
            return Pico4824A(simulate=True), True
        with self._lock:
            if self._hardware_device is None:
                self._hardware_device = Pico4824A(simulate=False)
            return self._hardware_device, False

    def initialize_hardware(self) -> None:
        """Open hardware once at Web startup and establish a 0 V AWG state."""
        device, _temporary = self._device_for_task(False)
        device.open()
        with self._lock:
            if self.status.state == "idle":
                self.status.message = "设备已连接；AWG 已置为 0 V，等待采集"

    def _capture_worker(
        self, capture_id: int, config: AcquisitionConfig, simulate: bool
    ) -> None:
        device, temporary = self._device_for_task(simulate)
        with self._lock:
            self.device = device
        try:
            if temporary:
                with device:
                    result = device.capture(config)
            else:
                device.open()
                result = device.capture(config)
            overflow = (
                "；输入溢出：" + ",".join(result.overflow_channels)
                if result.overflow_channels
                else ""
            )
            with self._lock:
                if self.status.capture_id == capture_id:
                    if self._stop_event.is_set():
                        self.status.state = "stopped"
                        self.status.message = "采集已停止"
                        self.status.finished_at = time.time()
                        return
                    self.result = result
                    self.status.state = "complete"
                    self.status.message = (
                        f"采集完成：{result.samples:,} 点/通道，"
                        f"{result.actual_sample_rate_hz / 1e6:.6g} MS/s{overflow}"
                    )
                    self.status.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                if self.status.capture_id == capture_id:
                    self.status.state = "stopped" if self._stop_event.is_set() else "error"
                    self.status.message = "采集已停止" if self._stop_event.is_set() else str(exc)
                    self.status.finished_at = time.time()
        finally:
            with self._lock:
                if self.device is device:
                    self.device = None
                if self._worker_thread is threading.current_thread():
                    self._worker_thread = None

    def _sweep_worker(
        self,
        task_id: int,
        config: AcquisitionConfig,
        sweep: SweepConfig,
        simulate: bool,
    ) -> None:
        device, temporary = self._device_for_task(simulate)
        with self._lock:
            self.device = device

        def update(progress: dict[str, Any], result: CaptureResult | None) -> None:
            with self._lock:
                if self.status.capture_id != task_id:
                    return
                self.status.progress = progress
                if result is not None:
                    self.result = result
                phase = "采集" if progress["phase"] == "capturing" else "已保存"
                self.status.message = (
                    f"扫描 {progress['run_index']}/{progress['total_runs']}："
                    f"{progress['frequency_hz'] / 1000:g} kHz，"
                    f"{progress['cycles']} 周期，第 {progress['repeat']}/{progress['repeats']} 次，"
                    f"{phase}"
                )

        try:
            if temporary:
                with device:
                    outcome = execute_sweep(
                        config,
                        sweep,
                        device,
                        self.sweep_output_root,
                        self._stop_event,
                        update,
                    )
            else:
                device.open()
                outcome = execute_sweep(
                    config,
                    sweep,
                    device,
                    self.sweep_output_root,
                    self._stop_event,
                    update,
                )
            with self._lock:
                if self.status.capture_id == task_id:
                    self.sweep_result = outcome
                    self.status.state = "stopped" if outcome.stopped else "complete"
                    self.status.message = (
                        f"扫描已停止，已保留 {len(outcome.run_rows)} 次采集：{outcome.directory}"
                        if outcome.stopped
                        else f"扫描完成，共 {len(outcome.run_rows)} 次采集：{outcome.directory}"
                    )
                    self.status.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                if self.status.capture_id == task_id:
                    self.status.state = "stopped" if self._stop_event.is_set() else "error"
                    self.status.message = "扫描已停止" if self._stop_event.is_set() else str(exc)
                    self.status.finished_at = time.time()
        finally:
            with self._lock:
                if self.device is device:
                    self.device = None
                if self._worker_thread is threading.current_thread():
                    self._worker_thread = None

    def _lcr_worker(
        self,
        task_id: int,
        config: AcquisitionConfig,
        lcr: LcrConfig,
        simulate: bool,
        calibration_standard_ohm: float | None,
    ) -> None:
        device, temporary = self._device_for_task(simulate)
        with self._lock:
            self.device = device

        def update(progress: dict[str, Any], result: CaptureResult | None) -> None:
            with self._lock:
                if self.status.capture_id != task_id:
                    return
                self.status.progress = progress
                if result is not None:
                    self.result = result
                phase = {
                    "capturing": "采集",
                    "auto_range": f"自动量程重采第 {progress.get('range_attempt', 2)} 次",
                    "saved": "已分析并保存",
                }.get(progress["phase"], progress["phase"])
                label = "电阻校准" if calibration_standard_ohm is not None else "LCR"
                self.status.message = (
                    f"{label} {progress['run_index']}/{progress['total_runs']}："
                    f"{progress['frequency_hz'] / 1000:g} kHz，"
                    f"第 {progress['repeat']}/{progress['repeats']} 次，{phase}"
                )

        try:
            if temporary:
                with device:
                    outcome = execute_lcr(
                        config,
                        lcr,
                        device,
                        self.lcr_output_root,
                        self._stop_event,
                        update,
                        calibration_standard_ohm,
                    )
            else:
                device.open()
                outcome = execute_lcr(
                    config,
                    lcr,
                    device,
                    self.lcr_output_root,
                    self._stop_event,
                    update,
                    calibration_standard_ohm,
                )
            with self._lock:
                if self.status.capture_id == task_id:
                    self.lcr_result = outcome
                    self.status.state = "stopped" if outcome.stopped else "complete"
                    if outcome.stopped:
                        self.status.message = (
                            f"LCR 已停止，已保留 {len(outcome.run_rows)} 次测量："
                            f"{outcome.directory}"
                        )
                    elif outcome.calibration_file is not None:
                        self.status.message = (
                            f"精准电阻校准完成：{outcome.calibration_file}"
                        )
                    else:
                        self.status.message = (
                            f"LCR 完成，共 {len(outcome.run_rows)} 次测量："
                            f"{outcome.directory}"
                        )
                    self.status.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                if self.status.capture_id == task_id:
                    self.status.state = "stopped" if self._stop_event.is_set() else "error"
                    self.status.message = "LCR 已停止" if self._stop_event.is_set() else str(exc)
                    self.status.finished_at = time.time()
        finally:
            with self._lock:
                if self.device is device:
                    self.device = None
                if self._worker_thread is threading.current_thread():
                    self._worker_thread = None

    def _big_lcr_worker(
        self,
        task_id: int,
        config: AcquisitionConfig,
        big_lcr: BigSignalLcrConfig,
        simulate: bool,
    ) -> None:
        device, temporary = self._device_for_task(simulate)
        with self._lock:
            self.device = device

        def update(progress: dict[str, Any], result: CaptureResult | None) -> None:
            with self._lock:
                if self.status.capture_id != task_id:
                    return
                self.status.progress = progress
                if result is not None:
                    # /api/result/display intentionally remains the shared latest
                    # waveform endpoint; tasks are mutually exclusive.
                    self.result = result
                phase = "采集" if progress["phase"] == "capturing" else "已分析并保存"
                safety = progress.get("safety_state")
                suffix = f"，安全状态 {safety}" if safety else ""
                self.status.message = (
                    f"大信号 LCR {progress['run_index']}/{progress['total_runs']}："
                    f"{progress['frequency_hz'] / 1000:g} kHz × "
                    f"{progress.get('awg_drive_vpp', 0):g} Vpp，"
                    f"第 {progress['repeat']}/{progress['repeats']} 次，{phase}{suffix}"
                )

        try:
            if temporary:
                with device:
                    outcome = execute_big_signal_lcr(
                        config,
                        big_lcr,
                        device,
                        self.big_lcr_output_root,
                        self._stop_event,
                        update,
                    )
            else:
                device.open()
                outcome = execute_big_signal_lcr(
                    config,
                    big_lcr,
                    device,
                    self.big_lcr_output_root,
                    self._stop_event,
                    update,
                )
            with self._lock:
                if self.status.capture_id == task_id:
                    self.big_lcr_result = outcome
                    self.status.state = "stopped" if outcome.stopped else "complete"
                    if outcome.safety_tripped:
                        reason = outcome.run_rows[-1].get("safety_message", "") if outcome.run_rows else ""
                        self.status.message = (
                            f"大信号 LCR 保护性停止（{reason}），已保留 {len(outcome.run_rows)} 次测量："
                            f"{outcome.directory}"
                        )
                    elif outcome.stopped:
                        self.status.message = (
                            f"大信号 LCR 已停止，已保留 {len(outcome.run_rows)} 次测量："
                            f"{outcome.directory}"
                        )
                    else:
                        self.status.message = (
                            f"大信号 LCR 完成，共 {len(outcome.run_rows)} 次测量："
                            f"{outcome.directory}"
                        )
                    self.status.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                if self.status.capture_id == task_id:
                    self.status.state = "stopped" if self._stop_event.is_set() else "error"
                    self.status.message = (
                        "大信号 LCR 已停止" if self._stop_event.is_set() else str(exc)
                    )
                    self.status.finished_at = time.time()
        finally:
            with self._lock:
                if self.device is device:
                    self.device = None
                if self._worker_thread is threading.current_thread():
                    self._worker_thread = None

    def stop(self) -> None:
        with self._lock:
            device = self.device
            if self.status.state != "running":
                raise RuntimeError("当前没有正在运行的采集")
            self._stop_event.set()
            self.status.message = {
                "sweep": "正在停止扫描",
                "lcr": "正在停止 LCR 测量",
                "lcr_calibration": "正在停止精准电阻校准",
                "big_lcr": "正在停止大信号 LCR 测量",
            }.get(self.status.task_kind, "正在停止采集")
        if device is not None:
            device.stop()

    def shutdown(self) -> None:
        """Safely zero and close the persistent hardware when Web exits."""
        with self._lock:
            self._stop_event.set()
            active = self.device
            worker = self._worker_thread
            hardware = self._hardware_device
        if active is not None:
            try:
                active.stop()
            except Exception:
                pass
        if (
            worker is not None
            and worker is not threading.current_thread()
            and worker.is_alive()
        ):
            worker.join(timeout=5.0)
        if hardware is not None:
            try:
                hardware.close()
            finally:
                with self._lock:
                    if self._hardware_device is hardware:
                        self._hardware_device = None
                    if self.device is hardware:
                        self.device = None

    def result_payload(
        self,
        max_points: int = 2400,
        display_filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            result = self.result
        if result is None:
            raise RuntimeError("尚无采集结果")
        filter_raw = display_filter or {}
        filter_enabled = bool(filter_raw.get("enabled", False))
        low_hz = float(filter_raw.get("low_hz", 20_000.0))
        high_hz = float(filter_raw.get("high_hz", 180_000.0))
        transition_hz = float(filter_raw.get("transition_hz", 5_000.0))
        display_volts = result.volts
        if filter_enabled:
            display_volts = {
                name: fft_bandpass(
                    values,
                    result.actual_sample_rate_hz,
                    low_hz,
                    high_hz,
                    transition_hz,
                )
                for name, values in result.volts.items()
            }
        step = max(1, int(np.ceil(result.samples / max_points)))
        indices = np.arange(0, result.samples, step, dtype=np.int64)
        return {
            "time_s": result.time_s[indices].tolist(),
            "channels": {
                name: values[indices].tolist() for name, values in display_volts.items()
            },
            "summary": {
                "samples": result.samples,
                "actual_sample_rate_hz": result.actual_sample_rate_hz,
                "requested_sample_rate_hz": result.requested_sample_rate_hz,
                "sample_interval_s": result.sample_interval_s,
                "overflow_channels": result.overflow_channels,
                "simulated": result.simulated,
                "display_step": step,
                "display_filter": {
                    "enabled": filter_enabled,
                    "low_hz": low_hz,
                    "high_hz": high_hz,
                    "transition_hz": transition_hz,
                },
            },
        }

    def save(self, kind: str) -> Path:
        with self._lock:
            result = self.result
        if result is None:
            raise RuntimeError("尚无可保存的采集结果")
        stem = PROJECT_DIR / "data" / default_stem()
        if kind == "npz":
            return save_npz(result, stem.with_suffix(".npz"))
        if kind == "csv":
            return save_csv(result, stem.with_suffix(".csv"))
        raise ValueError("保存格式必须是 npz 或 csv")

    def sweep_result_payload(self) -> dict[str, Any]:
        with self._lock:
            outcome = self.sweep_result
        if outcome is None:
            raise RuntimeError("尚无扫描结果")
        return outcome.payload()

    def lcr_result_payload(self) -> dict[str, Any]:
        with self._lock:
            outcome = self.lcr_result
        if outcome is None:
            raise RuntimeError("尚无 LCR 测量结果")
        return outcome.payload()

    def big_lcr_result_payload(self) -> dict[str, Any]:
        with self._lock:
            outcome = self.big_lcr_result
        if outcome is None:
            raise RuntimeError("尚无大信号 LCR 测量结果")
        return outcome.payload()

    def _linearity_directory(self, supplied: str) -> Path:
        root = self.big_lcr_output_root.resolve()
        if supplied.strip():
            directory = Path(supplied).resolve()
        else:
            with self._lock:
                latest = self.big_lcr_result.directory if self.big_lcr_result else None
            folders = sorted(root.glob("big_lcr_*"), reverse=True) if root.is_dir() else []
            directory = latest.resolve() if latest else (folders[0].resolve() if folders else root)
        if not directory.is_relative_to(root):
            raise ValueError("大信号线性度分析仅允许读取本项目 data/lcr_big 下的测量目录")
        return directory

    def big_lcr_linearity_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        directory = self._linearity_directory(str(raw.get("directory", "")))
        return analyze_linearity_directory(directory, int(raw.get("harmonic_order", 5)))

    def big_lcr_linearity_run_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        directory = self._linearity_directory(str(raw.get("directory", "")))
        return linearity_run_preview(directory, int(raw.get("run_index", 0)))

    def lcr_calibrations_payload(self) -> dict[str, Any]:
        return {"calibrations": discover_lcr_calibrations(self.lcr_output_root)}

    def sweep_run_payload(self, run_index: int, max_points: int = 3000) -> dict[str, Any]:
        """Load one saved sweep repetition for an on-demand waveform preview."""
        with self._lock:
            outcome = self.sweep_result
        if outcome is None:
            raise RuntimeError("尚无扫描结果")
        row = next(
            (item for item in outcome.run_rows if int(item["run_index"]) == run_index),
            None,
        )
        if row is None:
            raise ValueError(f"扫描中不存在第 {run_index} 次采集")
        root = outcome.directory.resolve()
        npz_path = (root / str(row["npz_file"])).resolve()
        if root not in npz_path.parents or not npz_path.is_file():
            raise RuntimeError("该次采集的原始 NPZ 文件不存在或路径无效")
        with np.load(npz_path, allow_pickle=False) as archive:
            time_s = np.asarray(archive["time_s"], dtype=np.float64)
            channel_names = sorted(
                key[3:-2] for key in archive.files if key.startswith("ch_") and key.endswith("_v")
            )
            step = max(1, int(np.ceil(time_s.size / max_points)))
            indices = np.arange(0, time_s.size, step, dtype=np.int64)
            channels = {
                name: np.asarray(archive[f"ch_{name}_v"], dtype=np.float64)[indices].tolist()
                for name in channel_names
            }
        return {
            "run": row,
            "file": str(npz_path),
            "time_s": time_s[indices].tolist(),
            "channels": channels,
            "summary": {
                "samples": int(time_s.size),
                "display_step": step,
            },
        }

def awg_preview_payload(raw: dict[str, Any], max_points: int = 2400) -> dict[str, Any]:
    config = AwgConfig(**raw)
    values, repetition_hz = normalized_waveform(config)
    step = max(1, int(np.ceil(values.size / max_points)))
    indices = np.arange(0, values.size, step, dtype=np.int64)
    duration_s = 1.0 / repetition_hz
    time_s = indices.astype(np.float64) / values.size * duration_s
    volts = config.offset_v + values[indices] * config.pk_to_pk_v / 2.0
    return {
        "time_s": time_s.tolist(),
        "volts": volts.tolist(),
        "duration_s": duration_s,
        "buffer_samples": values.size,
        "display_step": step,
    }


class PicoWebHandler(BaseHTTPRequestHandler):
    server_version = "Pico4824AWeb/0.1"

    @property
    def control(self) -> WebControlState:
        return self.server.control  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}")

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("请求内容为空或过大")
        raw = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("请求必须是 JSON 对象")
        return raw

    def _send_asset(self, name: str, content_type: str) -> None:
        path = WEB_DIR / name
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in {
                "/",
                "/measure",
                "/sweep",
                "/analysis",
                "/file-analysis",
                "/sweep-analysis",
                "/lcr",
                "/lcr-linearity",
            }:
                self._send_asset("index.html", "text/html; charset=utf-8")
            elif path == "/app.js":
                self._send_asset("app.js", "text/javascript; charset=utf-8")
            elif path == "/styles.css":
                self._send_asset("styles.css", "text/css; charset=utf-8")
            elif path == "/api/config":
                self._send_json(AcquisitionConfig().to_dict())
            elif path == "/api/status":
                self._send_json(self.control.snapshot())
            elif path == "/api/result":
                self._send_json(self.control.result_payload())
            elif path == "/api/sweep/result":
                self._send_json(self.control.sweep_result_payload())
            elif path == "/api/lcr/result":
                self._send_json(self.control.lcr_result_payload())
            elif path == "/api/big-lcr/result":
                self._send_json(self.control.big_lcr_result_payload())
            elif path == "/api/lcr/calibrations":
                self._send_json(self.control.lcr_calibrations_payload())
            elif path == "/api/sweep/run":
                query = parse_qs(parsed.query)
                run_index = int(query.get("run_index", ["0"])[0])
                self._send_json(self.control.sweep_run_payload(run_index))
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/capture":
                capture_id = self.control.start_capture(payload)
                self._send_json({"capture_id": capture_id}, HTTPStatus.ACCEPTED)
            elif path == "/api/sweep/start":
                task_id = self.control.start_sweep(payload)
                self._send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)
            elif path == "/api/lcr/start":
                task_id = self.control.start_lcr(payload)
                self._send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)
            elif path == "/api/lcr/calibrate":
                task_id = self.control.start_lcr_calibration(payload)
                self._send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)
            elif path == "/api/big-lcr/start":
                task_id = self.control.start_big_lcr(payload)
                self._send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)
            elif path == "/api/big-lcr/linearity":
                self._send_json(self.control.big_lcr_linearity_payload(payload))
            elif path == "/api/big-lcr/linearity/run":
                self._send_json(self.control.big_lcr_linearity_run_payload(payload))
            elif path == "/api/result/display":
                max_points = max(200, min(int(payload.get("max_points", 6000)), 20_000))
                self._send_json(
                    self.control.result_payload(
                        max_points=max_points,
                        display_filter=dict(payload.get("filter", {})),
                    )
                )
            elif path == "/api/awg-preview":
                self._send_json(awg_preview_payload(payload))
            elif path == "/api/analysis/browse":
                self._send_json(discover_sources(str(payload.get("path", ""))))
            elif path == "/api/analysis/process":
                source = str(payload.get("path", ""))
                self._send_json(process_dataset(source, payload))
            elif path == "/api/analysis/run-preview":
                source = str(payload.get("path", ""))
                time_s, channels, metadata = load_dataset(source)
                max_points = max(100, min(int(payload.get("max_points", 4000)), 20_000))
                step = max(1, int(np.ceil(time_s.size / max_points)))
                indices = np.arange(0, time_s.size, step, dtype=np.int64)
                self._send_json(
                    {
                        "time_s": time_s[indices].tolist(),
                        "channels": {
                            name: values[indices].tolist()
                            for name, values in channels.items()
                        },
                        "metadata": metadata,
                        "display_step": step,
                    }
                )
            elif path == "/api/analysis/sweep-folder":
                self._send_json(load_sweep_directory(str(payload.get("path", ""))))
            elif path == "/api/analysis/sweep-reevaluate":
                self._send_json(
                    reevaluate_sweep_directory(
                        str(payload.get("path", "")),
                        dict(payload.get("evaluation", {})),
                    )
                )
            elif path == "/api/analysis/time-frequency":
                source = str(payload.get("path", ""))
                time_s, channels, metadata = load_dataset(source)
                channel = str(payload.get("channel", "A")).upper()
                if channel not in channels:
                    raise ValueError(f"数据中不存在通道 {channel}")
                values = channels[channel]
                filter_raw = dict(payload.get("filter", {}))
                if bool(filter_raw.get("enabled", False)):
                    values = fft_bandpass(
                        values,
                        float(metadata["sample_rate_hz"]),
                        float(filter_raw.get("low_hz", 20_000.0)),
                        float(filter_raw.get("high_hz", 180_000.0)),
                        float(filter_raw.get("transition_hz", 5_000.0)),
                    )
                self._send_json(time_frequency_map(time_s, values, payload))
            elif path == "/api/analysis/experimental-modes":
                source = str(payload.get("path", ""))
                time_s, channels, metadata = load_dataset(source)
                reference_name = str(payload.get("reference_channel", "A")).upper()
                comparison_name = str(payload.get("comparison_channel", "G")).upper()
                if reference_name == comparison_name:
                    raise ValueError("共模/差模分析必须选择两个不同的通道")
                missing = [
                    name for name in (reference_name, comparison_name)
                    if name not in channels
                ]
                if missing:
                    raise ValueError(
                        "数据中不存在所选通道：" + ",".join(missing)
                    )
                reference_channel = channels[reference_name]
                comparison_channel = channels[comparison_name]
                filter_raw = dict(payload.get("filter", {}))
                if bool(filter_raw.get("enabled", False)):
                    filtered_channels: dict[str, np.ndarray] = {}
                    for name, values in (
                        (reference_name, reference_channel),
                        (comparison_name, comparison_channel),
                    ):
                        filtered = fft_bandpass(
                            values,
                            float(metadata["sample_rate_hz"]),
                            float(filter_raw.get("low_hz", 20_000.0)),
                            float(filter_raw.get("high_hz", 180_000.0)),
                            float(filter_raw.get("transition_hz", 5_000.0)),
                        )
                        filtered_channels[name] = filtered
                    reference_channel = filtered_channels[reference_name]
                    comparison_channel = filtered_channels[comparison_name]
                self._send_json(
                    experimental_mode_analysis(
                        time_s, reference_channel, comparison_channel, payload
                    )
                )
            elif path == "/api/analysis/export":
                source = str(payload.get("path", ""))
                output_format = str(payload.get("format", "npz")).lower()
                saved = export_filtered(
                    source,
                    dict(payload.get("filter", {})),
                    output_format,
                )
                self._send_json({"path": str(saved)})
            elif path == "/api/stop":
                self.control.stop()
                self._send_json({"ok": True})
            elif path == "/api/save":
                saved = self.control.save(str(payload.get("format", "npz")))
                self._send_json({"path": str(saved)})
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def serve(host: str = "127.0.0.1", port: int = 4824) -> None:
    server = ThreadingHTTPServer((host, port), PicoWebHandler)
    server.control = WebControlState()  # type: ignore[attr-defined]
    try:
        server.control.initialize_hardware()  # type: ignore[attr-defined]
    except Exception as exc:
        # Keep simulation and analysis pages usable when hardware is absent.
        # The first real acquisition will retry opening the same device object.
        with server.control._lock:  # type: ignore[attr-defined]
            server.control.status.message = (  # type: ignore[attr-defined]
                f"设备预连接失败（实机采集时将重试）：{exc}"
            )
    print(f"PicoScope 4824A Web 控制台：http://{host}:{port}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.control.shutdown()  # type: ignore[attr-defined]
        finally:
            server.server_close()


if __name__ == "__main__":
    serve()
