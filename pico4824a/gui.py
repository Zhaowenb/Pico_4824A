"""Small dependency-free Tk desktop interface for lab operation."""

from __future__ import annotations

from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from .config import AcquisitionConfig, AwgConfig, ChannelConfig, RANGE_VOLTS, TriggerConfig
from .device import CaptureResult, Pico4824A
from .storage import save_csv, save_npz
from .storage_naming import capture_stem


PROJECT_DIR = Path(__file__).resolve().parent.parent
COLORS = ("#4fc3f7", "#ffb74d", "#81c784", "#e57373", "#ba68c8", "#4dd0e1", "#fff176", "#90a4ae")


class ControlApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PicoScope 4824A · 8通道同步采集与AWG控制")
        self.root.geometry("1280x820")
        self.result: CaptureResult | None = None
        self.device: Pico4824A | None = None
        self._build()

    def _build(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        outer = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        controls = ttk.Frame(outer, width=325)
        display = ttk.Frame(outer)
        outer.add(controls, weight=0)
        outer.add(display, weight=1)

        adc = ttk.LabelFrame(controls, text="ADC同步采集", padding=10)
        adc.pack(fill=tk.X, pady=(0, 7))
        self.sample_rate = tk.StringVar(value="20")
        self.capture_duration_us = tk.StringVar(value="10000")
        self.trigger_position_percent = tk.StringVar(value="10")
        self._row(adc, 0, "采样率 (MS/s)", ttk.Entry(adc, textvariable=self.sample_rate, width=13))
        self._row(adc, 1, "采样时间 (μs)", ttk.Entry(adc, textvariable=self.capture_duration_us, width=13))
        self._row(adc, 2, "触发位置 (%)", ttk.Entry(adc, textvariable=self.trigger_position_percent, width=13))
        channel_frame = ttk.Frame(adc)
        channel_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.channel_vars: dict[str, tk.BooleanVar] = {}
        self.channel_ranges: dict[str, tk.StringVar] = {}
        for index, name in enumerate("ABCDEFGH"):
            var = tk.BooleanVar(value=True)
            range_var = tk.StringVar(value="5V")
            self.channel_vars[name] = var
            self.channel_ranges[name] = range_var
            row = (index // 4) * 2
            column = index % 4
            ttk.Checkbutton(channel_frame, text=name, variable=var).grid(row=row, column=column)
            ttk.Combobox(
                channel_frame,
                textvariable=range_var,
                values=tuple(RANGE_VOLTS),
                state="readonly",
                width=5,
            ).grid(row=row + 1, column=column, padx=2, pady=(0, 3))

        trig = ttk.LabelFrame(controls, text="硬件触发", padding=10)
        trig.pack(fill=tk.X, pady=7)
        self.trigger_enabled = tk.BooleanVar(value=True)
        self.trigger_source = tk.StringVar(value="H")
        self.trigger_level = tk.StringVar(value="0.1")
        ttk.Checkbutton(trig, text="启用", variable=self.trigger_enabled).grid(row=0, column=0, sticky="w")
        self._row(trig, 1, "触发通道", ttk.Combobox(trig, textvariable=self.trigger_source, values=tuple("ABCDEFGH"), state="readonly", width=10))
        self._row(trig, 2, "上升沿电平 (V)", ttk.Entry(trig, textvariable=self.trigger_level, width=13))

        awg = ttk.LabelFrame(controls, text="AWG激励", padding=10)
        awg.pack(fill=tk.X, pady=7)
        self.awg_enabled = tk.BooleanVar(value=True)
        self.awg_waveform = tk.StringVar(value="hann_burst")
        self.awg_frequency = tk.StringVar(value="100")
        self.awg_cycles = tk.StringVar(value="5")
        self.awg_vpp = tk.StringVar(value="2.0")
        ttk.Checkbutton(awg, text="启用", variable=self.awg_enabled).grid(row=0, column=0, sticky="w")
        self._row(awg, 1, "波形", ttk.Combobox(awg, textvariable=self.awg_waveform, values=("hann_burst", "sine", "square", "triangle"), state="readonly", width=12))
        self._row(awg, 2, "频率 (kHz)", ttk.Entry(awg, textvariable=self.awg_frequency, width=13))
        self._row(awg, 3, "正弦周期数", ttk.Entry(awg, textvariable=self.awg_cycles, width=13))
        self._row(awg, 4, "峰峰值 (V)", ttk.Entry(awg, textvariable=self.awg_vpp, width=13))

        run = ttk.LabelFrame(controls, text="运行", padding=10)
        run.pack(fill=tk.X, pady=7)
        self.simulate = tk.BooleanVar(value=False)
        ttk.Checkbutton(run, text="仿真模式（不连接硬件）", variable=self.simulate).pack(anchor="w")
        button_row = ttk.Frame(run)
        button_row.pack(fill=tk.X, pady=(8, 0))
        self.capture_button = ttk.Button(button_row, text="开始采集", command=self.start_capture)
        self.capture_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(button_row, text="停止", command=self.stop).pack(side=tk.LEFT, expand=True, fill=tk.X)
        save_row = ttk.Frame(run)
        save_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(save_row, text="保存 NPZ", command=lambda: self.save("npz")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(save_row, text="保存 CSV", command=lambda: self.save("csv")).pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.status = tk.StringVar(value="就绪；实机同步采集前请确认 AWG/监测输出已接至触发通道")
        ttk.Label(controls, textvariable=self.status, wraplength=300, foreground="#245a83").pack(fill=tk.X, pady=8)

        top = ttk.Frame(display)
        top.pack(fill=tk.X)
        ttk.Label(top, text="8通道波形预览", font=("Microsoft YaHei UI", 13, "bold")).pack(side=tk.LEFT)
        self.summary = tk.StringVar(value="尚无采集数据")
        ttk.Label(top, textvariable=self.summary).pack(side=tk.RIGHT)
        self.canvas = tk.Canvas(display, background="#10151c", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.canvas.bind("<Configure>", lambda _event: self.draw())

    @staticmethod
    def _row(parent: ttk.Widget, row: int, label: str, widget: ttk.Widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        widget.grid(row=row, column=1, sticky="e", pady=3, padx=(8, 0))
        parent.columnconfigure(1, weight=1)

    def _config(self) -> AcquisitionConfig:
        sample_rate_hz = float(self.sample_rate.get()) * 1e6
        total_samples = max(
            2,
            round(
                float(self.sample_rate.get())
                * float(self.capture_duration_us.get())
            ),
        )
        trigger_ratio = float(self.trigger_position_percent.get()) / 100.0
        if not 0 <= trigger_ratio < 1:
            raise ValueError("触发位置必须在0%到100%之间，且不能等于100%")
        pre_trigger_samples = min(total_samples - 1, round(total_samples * trigger_ratio))
        channels = {
            name: ChannelConfig(enabled=var.get(), range=self.channel_ranges[name].get())
            for name, var in self.channel_vars.items()
        }
        cfg = AcquisitionConfig(
            sample_rate_hz=sample_rate_hz,
            pre_trigger_samples=pre_trigger_samples,
            post_trigger_samples=total_samples - pre_trigger_samples,
            channels=channels,
            trigger=TriggerConfig(
                enabled=self.trigger_enabled.get(),
                source=self.trigger_source.get(),
                threshold_v=float(self.trigger_level.get()),
                direction="rising",
            ),
            awg=AwgConfig(
                enabled=self.awg_enabled.get(),
                waveform=self.awg_waveform.get(),
                frequency_hz=float(self.awg_frequency.get()) * 1e3,
                cycles=int(self.awg_cycles.get()),
                pk_to_pk_v=float(self.awg_vpp.get()),
                trigger_source="software",
            ),
        )
        cfg.validate()
        return cfg

    def start_capture(self) -> None:
        try:
            config = self._config()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        self.capture_button.configure(state=tk.DISABLED)
        self.status.set("正在配置设备并等待采集……")
        thread = threading.Thread(target=self._capture_worker, args=(config,), daemon=True)
        thread.start()

    def _capture_worker(self, config: AcquisitionConfig) -> None:
        try:
            self.device = Pico4824A(simulate=self.simulate.get())
            with self.device:
                result = self.device.capture(config)
            self.root.after(0, self._capture_done, result, None)
        except Exception as exc:
            self.root.after(0, self._capture_done, None, exc)
        finally:
            self.device = None

    def _capture_done(self, result: CaptureResult | None, error: Exception | None) -> None:
        self.capture_button.configure(state=tk.NORMAL)
        if error is not None:
            self.status.set(f"采集失败：{error}")
            messagebox.showerror("采集失败", str(error))
            return
        assert result is not None
        self.result = result
        overflow = f"；溢出通道 {','.join(result.overflow_channels)}" if result.overflow_channels else ""
        mode = "仿真" if result.simulated else "实机"
        self.status.set(f"{mode}采集完成{overflow}")
        self.summary.set(
            f"{result.samples:,} 点/通道 · {result.actual_sample_rate_hz / 1e6:.4g} MS/s"
        )
        self.draw()

    def stop(self) -> None:
        if self.device is not None:
            try:
                self.device.stop()
                self.status.set("已发送停止命令")
            except Exception as exc:
                self.status.set(f"停止失败：{exc}")

    def draw(self) -> None:
        self.canvas.delete("all")
        if self.result is None:
            return
        width = max(self.canvas.winfo_width(), 200)
        height = max(self.canvas.winfo_height(), 200)
        names = tuple(self.result.volts)
        lane_h = height / len(names)
        max_points = max(width - 90, 200)
        step = max(1, self.result.samples // max_points)
        x = np.linspace(75, width - 12, self.result.time_s[::step].size)
        for index, name in enumerate(names):
            top = index * lane_h
            mid = top + lane_h / 2
            self.canvas.create_line(70, mid, width - 10, mid, fill="#2a3440")
            self.canvas.create_text(30, mid, text=f"CH {name}", fill=COLORS[index], font=("Segoe UI", 10, "bold"))
            data = self.result.volts[name][::step]
            peak = max(float(np.max(np.abs(data))), 1e-9)
            y = mid - data / peak * lane_h * 0.38
            points = np.column_stack((x, y)).ravel().tolist()
            self.canvas.create_line(*points, fill=COLORS[index], width=1)
            self.canvas.create_text(width - 54, top + 11, text=f"±{peak:.3g} V", fill="#9aa8b5", font=("Segoe UI", 8))
        trigger_x = 75 + (width - 87) * self.result.config.pre_trigger_samples / max(self.result.samples - 1, 1)
        self.canvas.create_line(trigger_x, 0, trigger_x, height, fill="#f06292", dash=(4, 3))
        self.canvas.create_text(trigger_x + 4, 9, text="t=0", anchor="w", fill="#f06292")

    def save(self, kind: str) -> None:
        if self.result is None:
            messagebox.showinfo("无数据", "请先完成一次采集")
            return
        if getattr(self, "_saved_result", None) is not self.result:
            self._saved_stem = capture_stem(self.result)
            self._saved_result = self.result
        suffix = f".{kind}"
        path = filedialog.asksaveasfilename(
            initialdir=self._saved_stem.parent,
            initialfile=self._saved_stem.name + suffix,
            defaultextension=suffix,
            filetypes=[(kind.upper(), "*" + suffix)],
        )
        if not path:
            return
        try:
            saved = save_npz(self.result, path) if kind == "npz" else save_csv(self.result, path)
        except (ValueError, OSError) as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.status.set(f"已保存：{saved}")


def main() -> None:
    root = tk.Tk()
    ControlApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
