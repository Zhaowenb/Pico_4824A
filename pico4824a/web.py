"""Local web control server for PicoScope 4824A.

The server intentionally binds to localhost by default. PicoSDK and the USB
device remain on the instrument computer; the browser is only the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse, quote

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
from .lcr_linearity import (
    LinearityConfig,
    analyze_linearity_directory as analyze_lcr_linearity_directory,
    execute_linearity_test,
    linearity_run_preview as lcr_linearity_run_preview,
    load_lcr_folder,
)
from .storage import load_npz
from .lcr import analyze_impedance, load_lcr_calibration
from .big_signal_lcr import analyze_big_signal_impedance
from .storage import save_csv, save_npz
from .storage_naming import capture_stem, preferences, save_preferences
from .sweep import SweepConfig, SweepOutcome, execute_sweep
from .waveforms import normalized_waveform
from .time_frequency import time_frequency_map
from . import interference
from .file_browser import list_directory


PROJECT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_DIR / "web"
INTERFERENCE_DIR = WEB_DIR / "interference"


@dataclass(slots=True)
class WebStatus:
    state: str = "idle"
    message: str = "系统就绪"
    started_at: float | None = None
    finished_at: float | None = None
    capture_id: int = 0
    task_kind: str = "capture"
    progress: dict[str, Any] | None = None


from .bias_scan.web_extension import BiasScanWebMixin


class WebControlState(BiasScanWebMixin):
    def __init__(
        self,
        sweep_output_root: Path | None = None,
        lcr_output_root: Path | None = None,
        big_lcr_output_root: Path | None = None,
        lcr_linearity_output_root: Path | None = None,
        interference_output_root: Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.init_bias_scan(PROJECT_DIR)
        self.status = WebStatus()
        self.result: CaptureResult | None = None
        self.sweep_result: SweepOutcome | None = None
        self.lcr_result: LcrOutcome | None = None
        self.big_lcr_result: BigSignalLcrOutcome | None = None
        self.lcr_linearity_result: dict[str, Any] | None = None
        self.device: Pico4824A | None = None
        self._hardware_device: Pico4824A | None = None
        self._worker_thread: threading.Thread | None = None
        self.sweep_output_root = sweep_output_root or PROJECT_DIR / "data" / "sweeps"
        self.lcr_output_root = lcr_output_root or PROJECT_DIR / "data" / "lcr"
        self.big_lcr_output_root = big_lcr_output_root or PROJECT_DIR / "data" / "lcr_big"
        self.lcr_linearity_output_root = lcr_linearity_output_root or PROJECT_DIR / "data" / "lcr_linearity"
        self.interference_output_root = interference_output_root or interference.ROOT
        self._stop_event = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._trip_alarm: dict[str, Any] | None = None

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
                "trip_alarm": dict(self._trip_alarm) if self._trip_alarm else None,
                "has_bias_result": self.bias_controller is not None,
                "bias_output_unknown": self.bias_unknown,
                "bias_power_state": self.bias_power.output_state if self.bias_power else "disconnected",
                "bias_cleanup_pending": self.bias_cleanup_pending,
                "has_result": self.result is not None,
                "has_sweep_result": self.sweep_result is not None,
                "has_lcr_result": self.lcr_result is not None,
                "has_big_lcr_result": self.big_lcr_result is not None,
                "has_lcr_linearity_result": self.lcr_linearity_result is not None,
            }

    def start_capture(self, raw: dict[str, Any]) -> int:
        values = dict(raw)
        simulate = bool(values.pop("simulate", False))
        config = AcquisitionConfig.from_dict(values)
        with self._lock:
            if self.status.state in {"running", "paused"} or self.bias_unknown or self.bias_cleanup_pending:
                raise RuntimeError("已有采集任务正在运行")
            self._stop_event.clear()
            self._resume_event.set()
            self._trip_alarm = None
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
            if self.status.state in {"running", "paused"} or self.bias_unknown or self.bias_cleanup_pending:
                raise RuntimeError("已有采集或扫描任务正在运行")
            self._stop_event.clear()
            self._resume_event.set()
            self._trip_alarm = None
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
            if self.status.state in {"running", "paused"} or self.bias_unknown or self.bias_cleanup_pending:
                raise RuntimeError("已有仪器任务正在运行")
            self._stop_event.clear()
            self._resume_event.set()
            self._trip_alarm = None
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
            if self.status.state in {"running", "paused"} or self.bias_unknown or self.bias_cleanup_pending:
                raise RuntimeError("已有仪器任务正在运行")
            self._stop_event.clear()
            self._resume_event.set()
            self._trip_alarm = None
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

    def start_lcr_linearity(self, raw: dict[str, Any]) -> int:
        """Start a separate three-channel linearity test inside the big LCR mode."""
        config_raw = raw.get("config")
        linearity_raw = raw.get("linearity")
        if not isinstance(config_raw, dict) or not isinstance(linearity_raw, dict):
            raise ValueError("线性度测试请求必须包含 config 和 linearity")
        if raw.get("safety_acknowledged") is not True:
            raise ValueError("开始大信号线性度测试前必须确认 ATA-2021B 安全参数")
        config_values = dict(config_raw)
        simulate = bool(config_values.pop("simulate", False))
        config = AcquisitionConfig.from_dict(config_values)
        linearity = LinearityConfig.from_dict(linearity_raw)
        with self._lock:
            if self.status.state in {"running", "paused"} or self.bias_unknown or self.bias_cleanup_pending:
                raise RuntimeError("已有仪器任务正在运行")
            self._stop_event.clear()
            self._resume_event.set()
            self._trip_alarm = None
            self.status.capture_id += 1
            task_id = self.status.capture_id
            self.status.state = "running"
            self.status.task_kind = "lcr_linearity"
            self.status.message = "大信号线性度测试已启动"
            self.status.started_at = time.time()
            self.status.finished_at = None
            total_points = len(linearity.scan_plan)
            self.status.progress = {"phase": "starting", "run_index": 0,
                                    "total_runs": total_points * linearity.repeats,
                                    "point_index": 0, "total_points": total_points}
            self.lcr_linearity_result = None
        worker = threading.Thread(target=self._lcr_linearity_worker,
                                  args=(task_id, config, linearity, simulate),
                                  daemon=True, name=f"pico-lcr-linearity-{task_id}")
        with self._lock:
            self._worker_thread = worker
        worker.start()
        return task_id

    def _lcr_linearity_worker(self, task_id: int, config: AcquisitionConfig,
                              linearity: LinearityConfig, simulate: bool) -> None:
        device: Pico4824A | None = None

        def update(progress: dict[str, Any], result: CaptureResult | None) -> None:
            with self._lock:
                if self.status.capture_id != task_id:
                    return
                self.status.progress = progress
                if result is not None:
                    self.result = result
                self.status.message = (
                    f"线性度 {progress['run_index']}/{progress['total_runs']}："
                    f"{progress.get('direction', '')} · {progress.get('awg_vpp', 0):g} Vpp · "
                    f"重复 {progress['repeat']}/{progress['repeats']}"
                )

        def on_trip(row: dict[str, Any]) -> bool:
            run_index = int(row.get("run_index") or 1)
            return self._pause_for_trip(task_id, {
                "task_kind": "lcr_linearity",
                "reason": row.get("monitor_message") or "Voltage / Current Monitor 检测到持续掉幅",
                "monitor_state": row.get("monitor_state", "SUSPECT_TRIP"),
                "run_index": row.get("run_index"),
                "total_runs": len(linearity.scan_plan) * linearity.repeats,
                "point_index": (run_index - 1) // linearity.repeats + 1,
                "total_points": len(linearity.scan_plan),
                "repeat": row.get("repeat"),
                "repeats": linearity.repeats,
                "frequency_hz": row.get("frequency_hz"),
                "awg_vpp": row.get("awg_vpp"),
                "monitor_min_ratio": row.get("monitor_min_ratio"),
                "voltage_channel": linearity.voltage_channel,
                "current_channel": linearity.current_channel,
            })
        try:
            device, temporary = self._device_for_task(simulate)
            with self._lock:
                self.device = device
            if temporary:
                with device:
                    outcome = execute_linearity_test(config, linearity, device,
                                                     self.lcr_linearity_output_root,
                                                     self._stop_event, update, on_trip)
            else:
                device.open()
                outcome = execute_linearity_test(config, linearity, device,
                                                 self.lcr_linearity_output_root,
                                                 self._stop_event, update, on_trip)
            with self._lock:
                if self.status.capture_id == task_id:
                    self.lcr_linearity_result = outcome
                    self.status.state = "stopped" if outcome.get("stopped") else "complete"
                    if outcome.get("safety_tripped") and not outcome.get("stopped"):
                        self.status.message = (
                            f"大信号线性度测试已恢复扫描并完成，期间处理过疑似跳闸；"
                            f"保存 {len(outcome['run_rows'])} 次采集：{outcome['directory']}"
                        )
                    else:
                        self.status.message = (
                            f"大信号线性度测试{'已停止' if outcome.get('stopped') else '完成'}，"
                            f"已保存 {len(outcome['run_rows'])} 次采集：{outcome['directory']}"
                        )
                    self.status.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                if self.status.capture_id == task_id:
                    self.status.state = "stopped" if self._stop_event.is_set() else "error"
                    self.status.message = "大信号线性度测试已停止" if self._stop_event.is_set() else str(exc)
                    self.status.finished_at = time.time()
        finally:
            with self._lock:
                if self.device is device:
                    self.device = None
                if self._worker_thread is threading.current_thread():
                    self._worker_thread = None

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
        device: Pico4824A | None = None

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

        def on_trip(row: dict[str, Any]) -> bool:
            run_index = int(row.get("run_index") or 1)
            return self._pause_for_trip(task_id, {
                "task_kind": "big_lcr",
                "reason": row.get("safety_message") or "Monitor 检测到平顶区持续掉幅",
                "monitor_state": row.get("safety_state", "SUSPECT_TRIP"),
                "run_index": run_index,
                "total_runs": len(big_lcr.parameter_points) * big_lcr.repeats,
                "point_index": (run_index - 1) // big_lcr.repeats + 1,
                "total_points": len(big_lcr.parameter_points),
                "repeat": row.get("repeat"),
                "repeats": big_lcr.repeats,
                "frequency_hz": row.get("frequency_hz"),
                "awg_vpp": row.get("awg_drive_vpp"),
                "monitor_min_ratio": row.get("monitor_min_ratio"),
                "voltage_channel": big_lcr.voltage_channel,
                "current_channel": big_lcr.current_channel,
            })

        try:
            device, temporary = self._device_for_task(simulate)
            with self._lock:
                self.device = device
            if temporary:
                with device:
                    outcome = execute_big_signal_lcr(
                        config,
                        big_lcr,
                        device,
                        self.big_lcr_output_root,
                        self._stop_event,
                        update,
                        on_trip,
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
                    on_trip,
                )
            with self._lock:
                if self.status.capture_id == task_id:
                    self.big_lcr_result = outcome
                    self.status.state = "stopped" if outcome.stopped else "complete"
                    if outcome.safety_tripped and outcome.stopped and not self._stop_event.is_set():
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
                    elif outcome.safety_tripped:
                        self.status.message = (
                            f"大信号 LCR 已恢复扫描并完成，期间处理过疑似跳闸；"
                            f"共保存 {len(outcome.run_rows)} 次测量：{outcome.directory}"
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

    def _pause_for_trip(self, task_id: int, alarm: dict[str, Any]) -> bool:
        """Pause between measurements and wait for an explicit operator decision."""
        with self._lock:
            if self.status.capture_id != task_id or self._stop_event.is_set():
                return False
            alarm["alarm_id"] = f"{task_id}-{time.time_ns()}"
            self._trip_alarm = alarm
            self._resume_event.clear()
            self.status.state = "paused"
            self.status.message = "疑似功放跳闸：扫描已暂停，等待人工检查并确认"
        while True:
            self._resume_event.wait(0.2)
            with self._lock:
                if self.status.capture_id != task_id or self._stop_event.is_set():
                    self._trip_alarm = None
                    return False
                if self.status.state == "running":
                    return True

    def resolve_trip(self) -> None:
        """Resume only after the operator explicitly confirms the problem is resolved."""
        with self._lock:
            if self.status.state != "paused" or self._trip_alarm is None:
                raise RuntimeError("当前没有等待处理的疑似跳闸")
            self._trip_alarm = None
            self.status.state = "running"
            self.status.message = "已人工确认问题解决，扫描将从下一测量点继续"
            self._resume_event.set()

    def stop(self) -> None:
        with self._lock:
            device = self.device
            was_paused = self.status.state == "paused"
            if self.status.state not in {"running", "paused"}:
                raise RuntimeError("当前没有正在运行的采集")
            self._stop_event.set()
            self._resume_event.set()
            if was_paused:
                self._trip_alarm = None
            self.status.message = {
                "sweep": "正在停止扫描",
                "lcr": "正在停止 LCR 测量",
                "lcr_calibration": "正在停止精准电阻校准",
                "big_lcr": "正在停止大信号 LCR 测量",
                "lcr_linearity": "正在停止大信号线性度测试",
            }.get(self.status.task_kind, "正在停止采集")
        if self.status.task_kind == "bias_scan" and self.bias_controller is not None:
            self.bias_controller.stop()
        if device is not None and not was_paused:
            device.stop()

    def shutdown(self) -> None:
        """Safely zero and close the persistent hardware when Web exits."""
        with self._lock:
            self._stop_event.set()
            self._resume_event.set()
            self._trip_alarm = None
            active = self.device
            worker = self._worker_thread
            hardware = self._hardware_device
        bias_error = None
        try:self.bias_shutdown()
        except Exception as exc:bias_error = exc
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

        if bias_error is not None:raise bias_error

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
        if kind not in {'npz', 'csv'}:
            raise ValueError('保存格式必须是 npz 或 csv')
        with self._lock:
            result = self.result
        if result is None:
            raise RuntimeError("尚无可保存的采集结果")
        with self._lock:
            if getattr(self, "_saved_capture_result", None) is not result:
                self._saved_capture_stem = capture_stem(result)
                self._saved_capture_result = result
            stem = self._saved_capture_stem
        if kind == "npz":
            return save_npz(result, stem.parent / (stem.name + ".npz"))
        if kind == "csv":
            return save_csv(result, stem.parent / (stem.name + ".csv"))
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

    def lcr_linearity_result_payload(self) -> dict[str, Any]:
        with self._lock:
            outcome = self.lcr_linearity_result
        if outcome is None:
            raise RuntimeError("尚无线性度测试结果")
        return outcome

    def _lcr_analysis_directory(self, mode: str, supplied: str) -> Path:
        roots = {"big_lcr": self.big_lcr_output_root, "linearity": self.lcr_linearity_output_root,
                 "small_lcr": self.lcr_output_root}
        if mode not in roots:
            raise ValueError("未知 LCR 文件夹分析模式")
        allowed_roots = [roots[mode].resolve()]
        if mode == "linearity":
            # Legacy Monitor-only analyses lived inside ordinary big_lcr_* folders.
            allowed_roots.append(self.big_lcr_output_root.resolve())
        from .data_access import REFERENCE_DATA, import_directory
        reference_modes = {"big_lcr": "lcr_big", "linearity": "lcr_linearity", "small_lcr": "lcr"}
        reference_roots = [REFERENCE_DATA / reference_modes[mode]]
        if mode == "linearity": reference_roots.append(REFERENCE_DATA / "lcr_big")
        if supplied.strip():
            directory = Path(supplied).resolve()
            if any(directory.is_relative_to(p.resolve()) for p in reference_roots):
                directory = import_directory(directory, roots[mode])
        else:
            marker = {"big_lcr": "big_lcr_config.json", "linearity": "linearity_config.json", "small_lcr": "lcr_config.json"}[mode]
            root = allowed_roots[0]
            folders = sorted((p.parent.resolve() for p in root.glob('*/'+marker)), key=lambda p:p.stat().st_mtime, reverse=True) if root.is_dir() else []
            if mode == "linearity" and not folders and allowed_roots[1].is_dir():
                folders = sorted((p.parent.resolve() for p in allowed_roots[1].glob('*/big_lcr_config.json')), key=lambda p:p.stat().st_mtime, reverse=True)
            if not folders:
                raise ValueError(f"{mode} 数据目录中没有可分析的测量文件夹")
            directory = folders[0]
        if not any(directory.is_relative_to(root) for root in allowed_roots):
            raise ValueError("文件夹必须位于本项目对应的 data/lcr_big、data/lcr_linearity 或 data/lcr 目录内")
        if not directory.is_dir():
            raise ValueError("所选 LCR 文件夹不存在")
        return directory

    def lcr_folder_analysis_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        mode = str(raw.get("mode", ""))
        directory = self._lcr_analysis_directory(mode, str(raw.get("directory", "")))
        reanalyze = bool(raw.get("reanalyze", False))
        settings = raw.get("settings", {})
        if not isinstance(settings, dict):
            raise ValueError("分析参数必须是 JSON 对象")
        return load_lcr_folder(mode, directory, reanalyze, settings)

    def lcr_folder_run_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        mode = str(raw.get("mode", ""))
        directory = self._lcr_analysis_directory(mode, str(raw.get("directory", "")))
        run_index = int(raw.get("run_index", -1))
        if mode == "linearity":
            settings = raw.get("settings", {})
            if not isinstance(settings, dict):
                raise ValueError("分析参数必须是 JSON 对象")
            if not (directory / "linearity_config.json").is_file():
                return linearity_run_preview(directory, run_index)
            return lcr_linearity_run_preview(directory, run_index, settings=settings or None)
        prefix = "big_lcr" if mode == "big_lcr" else "lcr"
        saved = json.loads((directory / f"{prefix}_config.json").read_text(encoding="utf-8"))
        with (directory / "runs.csv").open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        row = next((item for item in rows if int(float(item.get("run_index", -1))) == run_index), None)
        if row is None:
            raise ValueError("找不到所选的原始运行记录")
        relative = Path(row.get("npz_file", ""))
        path = (directory / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to((directory / "raw").resolve()) or not path.is_file():
            raise ValueError("原始 NPZ 路径无效")
        capture = load_npz(path)
        step = max(1, int(np.ceil(capture.samples / 5000)))
        indices = np.arange(0, capture.samples, step)
        return {"run": row, "directory": str(directory), "time_s": capture.time_s[indices].tolist(),
                "channels": {name: capture.volts[name][indices].tolist() for name in capture.volts},
                "metadata": {"sample_rate_hz": capture.actual_sample_rate_hz, "overflow_channels": capture.overflow_channels,
                             "config": saved.get("base_config", {})}}

    def _linearity_directory(self, supplied: str) -> Path:
        root = self.big_lcr_output_root.resolve()
        from .data_access import REFERENCE_DATA, import_directory
        if supplied.strip():
            directory = Path(supplied).resolve()
            if directory.is_relative_to((REFERENCE_DATA / 'lcr_big').resolve()):
                directory = import_directory(directory, root)
        else:
            with self._lock:
                latest = self.big_lcr_result.directory if self.big_lcr_result else None
            folders = sorted((p.parent for p in root.glob('*/big_lcr_config.json')), key=lambda p:p.stat().st_mtime, reverse=True) if root.is_dir() else []
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

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionError, TimeoutError):
            # Navigation, cancellation and socket teardown can disconnect the
            # client at any point, including while sending an error response.
            self.close_connection = True

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

    def _send_interference_asset(self, name: str, content_type: str) -> None:
        body = (INTERFERENCE_DIR / name).read_bytes()
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
            if path in {"/ui/workstation.js", "/ui/analysis-state.js", "/analysis/time-frequency.js", "/analysis/experimental.js", "/ui/storage-naming.js", "/ui/file-picker.js", "/views/signal-analysis.js", "/views/instruments.js", "/views/experiment.js", "/views/bias-scan.js"}:
                self._send_asset(path.lstrip("/"), "text/javascript; charset=utf-8")
            elif path == "/views/bias-scan.css":
                self._send_asset("views/bias-scan.css", "text/css; charset=utf-8")
            elif path == "/ui/workstation.css":
                self._send_asset("ui/workstation.css", "text/css; charset=utf-8")
            elif path in {"/interference", "/interference/"}:
                self._send_interference_asset("index.html", "text/html; charset=utf-8")
            elif path in {"/interference/manual", "/interference/manual.html"}:
                self._send_interference_asset("manual.html", "text/html; charset=utf-8")
            elif path == "/interference/app.js":
                self._send_interference_asset("app.js", "text/javascript; charset=utf-8")
            elif path == "/interference/guide.js":
                self._send_interference_asset("guide.js", "text/javascript; charset=utf-8")
            elif path == "/interference/manual.js":
                self._send_interference_asset("manual.js", "text/javascript; charset=utf-8")
            elif path == "/interference/styles.css":
                self._send_interference_asset("styles.css", "text/css; charset=utf-8")
            elif path == "/interference/manual.css":
                self._send_interference_asset("manual.css", "text/css; charset=utf-8")
            elif path == "/api/interference/session":
                session_id = parse_qs(parsed.query).get("id", [""])[0]
                session = interference.load_session(session_id, self.control.interference_output_root)
                self._send_json({"session": session, "summary": interference.summarize(session)})
            elif path == "/api/interference/preview":
                query = parse_qs(parsed.query)
                self._send_json(interference.preview(query.get("id", [""])[0], query.get("run", [""])[0], self.control.interference_output_root))
            elif path == "/api/interference/export":
                session_id = parse_qs(parsed.query).get("id", [""])[0]
                body = interference.export_csv(session_id, self.control.interference_output_root).read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                session_name=interference.load_session(session_id,self.control.interference_output_root).get('name','干扰实验')
                self.send_header("Content-Disposition", "attachment; filename=interference.csv; filename*=UTF-8''"+quote(session_name+'__逐次统计.csv'))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path in {
                "/",
                "/measure",
                "/bias-scan",
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
            elif path == "/api/bias-scan/resources":
                from .bias_scan import IT6524DController
                self._send_json({"resources":self.control.bias_resources()})
            elif path == "/api/bias-scan/result":
                self._send_json(self.control.bias_result_payload())
            elif path == "/api/bias-scan/preview":
                query=parse_qs(parsed.query)
                self._send_json(self.control.bias_preview(int(query.get("point",["0"])[0]),int(query.get("repeat",["1"])[0])))
            elif path == "/api/bias-scan/export":
                name=parse_qs(parsed.query).get("name",["summary.csv"])[0]
                if name == 'all.zip':
                    archive=self.control.bias_archive()
                    self.send_response(HTTPStatus.OK)
                    self.send_header('Content-Type','application/zip')
                    self.send_header('Content-Disposition', "attachment; filename=bias-scan.zip; filename*=UTF-8''"+quote(archive.name))
                    self.send_header('Content-Length', str(archive.stat().st_size))
                    self.end_headers()
                    with archive.open('rb') as stream:
                        while chunk:=stream.read(1024*1024):self.wfile.write(chunk)
                else:
                    body=self.control.bias_export(name)
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type","application/octet-stream")
                    labels={'summary.csv':'逐档统计.csv','runs.csv':'逐次记录.csv','result.json':'任务结果.json','config.json':'配置快照.json'}
                    job_name=self.control.bias_controller.folder.name
                    self.send_header("Content-Disposition", "attachment; filename=bias-"+name+"; filename*=UTF-8''"+quote(job_name+'__'+labels[name]))
                    self.send_header("Content-Length",str(len(body)))
                    self.end_headers();self.wfile.write(body)
            elif path == "/api/storage/archive":
                from .data_sessions import archive_session
                supplied = parse_qs(parsed.query).get('directory', [''])[0]
                folder = Path(supplied).resolve()
                data_root = (PROJECT_DIR / 'data').resolve()
                if not supplied or folder == data_root or not folder.is_relative_to(data_root) or 'exports' in folder.relative_to(data_root).parts:
                    raise ValueError('请选择本项目 data 下一个已保存任务的文件夹')
                with self.control._lock:
                    if self.control.status.state in {'running', 'paused'}:
                        raise RuntimeError('请等待仪器任务完成后打包')
                    archive = archive_session(folder)
                    self.send_response(HTTPStatus.OK)
                    self.send_header('Content-Type', 'application/zip')
                    self.send_header('Content-Disposition', "attachment; filename=waveguard-data.zip; filename*=UTF-8''"+quote(archive.name))
                    self.send_header('Content-Length', str(archive.stat().st_size))
                    self.end_headers()
                    with archive.open('rb') as stream:
                        while chunk := stream.read(1024*1024): self.wfile.write(chunk)
            elif path == "/api/storage/naming":
                self._send_json({"names": preferences()})
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
            elif path == "/api/lcr-linearity/result":
                self._send_json(self.control.lcr_linearity_result_payload())
            elif path == "/api/lcr/calibrations":
                self._send_json(self.control.lcr_calibrations_payload())
            elif path == "/api/sweep/run":
                query = parse_qs(parsed.query)
                run_index = int(query.get("run_index", ["0"])[0])
                self._send_json(self.control.sweep_run_payload(run_index))
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except ConnectionError:
            self.close_connection = True
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        try:
            if getattr(self.server, "shutdown_in_progress", False):
                self._send_json({"error": "服务正在关闭"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if path == "/api/admin/shutdown":
                # Only the local launcher may shut down the instrument server.
                # The non-simple header also prevents an unrelated web page
                # from submitting this request through a browser form.
                if (
                    self.client_address[0] not in {"127.0.0.1", "::1"}
                    or self.headers.get("Host", "").split(":")[0] != "127.0.0.1"
                    or self.headers.get("Origin") is not None
                    or self.headers.get("X-Pico-Local-Control") != "shutdown"
                ):
                    self._send_json({"error": "仅允许本机管理脚本关闭服务"}, HTTPStatus.FORBIDDEN)
                    return
                with self.control._lock:
                    if self.control.status.state in {"running", "paused"}:
                        raise RuntimeError("测量仍在运行；请先停止任务，再关闭服务")
                    self.server.shutdown_in_progress = True  # type: ignore[attr-defined]
                try:
                    self.control.shutdown()
                except Exception:
                    self.server.shutdown_in_progress = False  # type: ignore[attr-defined]
                    raise
                try:
                    self._send_json({"ok": True, "message": "设备已安全关闭，服务正在退出"})
                finally:
                    # Even if the launcher disconnects while receiving the
                    # reply, the cleaned-up server must still leave the port.
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            payload = self._read_json()
            if path == "/api/bias-scan/connect":
                self._send_json(self.control.bias_connect(payload))
            elif path == "/api/bias-scan/recover":
                self._send_json(self.control.bias_recover())
            elif path == "/api/bias-scan/preflight":
                self._send_json(self.control.bias_preflight(payload))
            elif path == "/api/bias-scan/start":
                self._send_json({"task_id":self.control.start_bias_scan(payload)}, HTTPStatus.ACCEPTED)
            elif path == "/api/interference/session":
                self._send_json({"session": interference.create_session(payload, self.control.interference_output_root)}, HTTPStatus.CREATED)
            elif path == "/api/interference/run":
                capture_id = int(payload["capture_id"])
                with self.control._lock:
                    if (self.control.status.capture_id != capture_id or
                        self.control.status.task_kind != "capture" or
                        self.control.status.state != "complete" or self.control.result is None):
                        raise RuntimeError("capture id is no longer the latest completed capture")
                    result = interference.save_run(str(payload["session_id"]), capture_id,
                        self.control.result, payload, self.control.interference_output_root)
                self._send_json(result)
            elif path == "/api/interference/reanalyze":
                self._send_json(interference.reanalyze(str(payload["session_id"]), payload["windows_us"], self.control.interference_output_root))
            elif path == "/api/interference/complete":
                session_id = str(payload["session_id"])
                with interference._LOCK:
                    session = interference.load_session(session_id, self.control.interference_output_root)
                    experiment = int(payload["experiment"])
                    if experiment not in range(1, 13): raise ValueError("invalid experiment")
                    if experiment not in session["completed_experiments"]:
                        session["completed_experiments"].append(experiment)
                    interference._write_json(interference._folder(session_id, self.control.interference_output_root) / "session.json", session)
                self._send_json({"session": session, "summary": interference.summarize(session)})
            elif path == "/api/capture":
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
            elif path == "/api/lcr-linearity/start":
                task_id = self.control.start_lcr_linearity(payload)
                self._send_json({"task_id": task_id}, HTTPStatus.ACCEPTED)
            elif path == "/api/lcr-analysis/load":
                self._send_json(self.control.lcr_folder_analysis_payload(payload))
            elif path == "/api/lcr-analysis/run-preview":
                self._send_json(self.control.lcr_folder_run_payload(payload))
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
            elif path == "/api/files/list":
                self._send_json(list_directory(str(payload.get("path", "")), PROJECT_DIR))
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
            elif path == "/api/trip/resolve":
                self.control.resolve_trip()
                self._send_json({"ok": True, "resumed": True})
            elif path == "/api/storage/naming":
                self._send_json({"names": save_preferences(payload.get("names", {}))})
            elif path == "/api/save":
                saved = self.control.save(str(payload.get("format", "npz")))
                self._send_json({"path": str(saved)})
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except ConnectionError:
            self.close_connection = True
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
