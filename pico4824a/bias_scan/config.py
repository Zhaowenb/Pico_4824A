from dataclasses import dataclass, asdict
from decimal import Decimal
import math
from ..config import AcquisitionConfig
from .limits import MAX_CURRENT_A


@dataclass(frozen=True)
class BiasScanConfig:
    start_a: float = 0.0
    stop_a: float = 6.0
    step_a: float = 0.5
    repeats: int = 10
    interval_s: float = 0.1
    record_duration_us: float = 2000.0
    pzt_channel: str = 'A'
    excitation_voltage_channel: str = ''
    excitation_current_channel: str = ''
    excitation_voltage_scale: float = 1.0
    excitation_current_scale: float = 1.0
    excitation_start_us: float = 0.0
    excitation_end_us: float = 100.0
    aggregation: str = 'trimmed_mean'
    amplitude_mode: str = 'snapshot'
    amplitude_order: str = 'bias_outer'
    amplitude_fixed_vpp: float = 2.0
    amplitude_start_vpp: float = 0.2
    amplitude_stop_vpp: float = 2.0
    amplitude_step_vpp: float = 0.2
    waveform_guard_enabled: bool = False
    auto_range_enabled: bool = True
    range_max_retries: int = 3
    excitation_ipp_limit_a: float = 12.0
    shape_residual_limit: float = 0.25
    repeat_change_fraction: float = 0.5
    trip_drop_ratio: float = 0.2
    monitor_min_rms_v: float = 0.002
    h2_enabled: bool = False
    h2_limit_pct: float = 5.0
    harmonic_band_fraction: float = 0.1
    direct_start_us: float = 100.0
    direct_end_us: float = 400.0
    filter_enabled: bool = True
    filter_low_hz: float = 60_000.0
    filter_high_hz: float = 90_000.0
    filter_transition_hz: float = 5_000.0
    protection_mode: str = 'independent'
    stable_abs_a: float = 0.05
    stable_rel: float = 0.01
    stable_hold_s: float = 0.2
    poll_s: float = 0.05
    voltage_limit_v: float | None = None
    max_on_s: float | None = None
    stable_timeout_s: float | None = None
    cooldown_s: float | None = None
    cooldown_min_s: float | None = None
    actual_current_limit_a: float = 6.0
    cooling_mode: str = 'fixed'
    temperature_limit_c: float | None = None
    temperature_resume_c: float | None = None
    temperature_max_age_s: float = 1.0
    temperature_wait_timeout_s: float = 300.0
    inductive_protection_confirmed: bool = False
    independent_cutoff_confirmed: bool = False
    protection_notes: str = ''
    memory_limit_mb: float = 256.0
    scan_name: str = ''

    @classmethod
    def from_dict(cls, raw):
        config = cls(**dict(raw))
        config.validate()
        return config

    def validate(self, live=False):
        flags = {"inductive_protection_confirmed", "independent_cutoff_confirmed", "filter_enabled", "waveform_guard_enabled", "auto_range_enabled", "h2_enabled"}
        text = {"pzt_channel", "cooling_mode", "protection_notes", "scan_name", "protection_mode", "excitation_voltage_channel", "excitation_current_channel", "aggregation", "amplitude_mode", "amplitude_order"}
        for name, value in asdict(self).items():
            if name in flags and not isinstance(value, bool):raise ValueError(f"{name} 必须为布尔值")
            if name in text and not isinstance(value, str):raise ValueError(f"{name} 必须为字符串")
            if name not in flags | text and value is not None and (isinstance(value,bool) or not isinstance(value,(int,float))):raise ValueError(f"{name} 必须为数值")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
                raise ValueError(f'{name} 必须是有限值')
        if self.scan_name:
            from ..storage_naming import component
            component(self.scan_name)
        if not 0 <= self.start_a <= self.stop_a <= MAX_CURRENT_A or not 0.01 <= self.step_a <= MAX_CURRENT_A:
            raise ValueError(f'扫描范围必须位于 0–{MAX_CURRENT_A:g} A，步长至少 0.01 A')
        if isinstance(self.repeats, bool) or not isinstance(self.repeats, int) or not 5 <= self.repeats <= 10:
            raise ValueError('每档采集次数必须是 5–10 的整数')
        if self.interval_s < 0 or self.interval_s > 10 or not 0 < self.record_duration_us <= 20000:
            raise ValueError('采集间隔或记录长度无效')
        if self.pzt_channel not in 'ABCDEFGH' or len(self.pzt_channel) != 1:
            raise ValueError('PZT 通道必须为 A–H')
        for channel in [self.excitation_voltage_channel, self.excitation_current_channel]:
            if channel and (len(channel) != 1 or channel not in 'ABCDEFGH'):
                raise ValueError('激励通道必须为 A–H 或留空')
        roles = [c for c in [self.pzt_channel, self.excitation_voltage_channel, self.excitation_current_channel] if c]
        if len(set(roles)) != len(roles):
            raise ValueError('PZT、激励电压和激励电流须选择不同通道')
        if min(self.excitation_voltage_scale, self.excitation_current_scale) <= 0:
            raise ValueError('探头换算系数必须大于 0')
        if self.excitation_end_us <= self.excitation_start_us:
            raise ValueError('激励窗口终点必须大于起点')
        if self.aggregation not in {'mean', 'trimmed_mean', 'median'}:
            raise ValueError('统计方式必须为 mean、trimmed_mean 或 median')
        if self.amplitude_mode not in {'snapshot','fixed','sweep'} or self.amplitude_order not in {'bias_outer','amplitude_outer'}:
            raise ValueError('激励幅值模式或扫描顺序无效')
        if not 0 < self.amplitude_fixed_vpp <= 4 or not 0 < self.amplitude_start_vpp <= self.amplitude_stop_vpp <= 4 or not .01 <= self.amplitude_step_vpp <= 4:
            raise ValueError('AWG 幅值必须位于 0–4 Vpp，步进至少 0.01 V')
        if isinstance(self.range_max_retries,bool) or not isinstance(self.range_max_retries,int) or not 0 <= self.range_max_retries <= 5:
            raise ValueError('量程重采次数必须为 0–5 的整数')
        if not 0 < self.excitation_ipp_limit_a <= 12 or not .05 <= self.shape_residual_limit <= .8 or not .05 <= self.repeat_change_fraction <= .8 or not .02 <= self.trip_drop_ratio <= .8 or not 0 < self.monitor_min_rms_v <= 1:
            raise ValueError('激励波形保护阈值无效，Ipp 硬上限为 12 A')
        if not 0 < self.h2_limit_pct <= 100 or not .02 <= self.harmonic_band_fraction <= .25:
            raise ValueError('二次谐波阈值或带宽无效')
        if self.waveform_guard_enabled and (not self.excitation_voltage_channel or not self.excitation_current_channel):
            raise ValueError('波形保护需要配置激励电压、电流通道及探头系数')
        if (self.amplitude_mode != 'snapshot' or self.h2_enabled) and not self.waveform_guard_enabled:
            raise ValueError('幅值调节/扫描和 H2 质量检测必须开启波形保护')
        if self.direct_end_us <= self.direct_start_us:
            raise ValueError('直达波窗口终点必须大于起点')
        if not 0 <= self.filter_low_hz < self.filter_high_hz or self.filter_transition_hz < 0:
            raise ValueError('带通参数必须满足 0 ≤ 下限 < 上限，过渡带不能为负')
        if self.protection_mode not in {'software', 'independent'}:
            raise ValueError('保护模式必须为 software 或 independent')
        if not 0 < self.stable_abs_a <= 0.5 or not 0 <= self.stable_rel <= .1:
            raise ValueError('稳定误差范围无效')
        if not 0.01 <= self.poll_s <= .2 or self.stable_hold_s < self.poll_s:
            raise ValueError('稳定保持时间必须至少覆盖一次查询周期')
        if not self.stop_a <= self.actual_current_limit_a <= MAX_CURRENT_A or self.actual_current_limit_a <= 0:
            raise ValueError(f'请设置实际电流上限以覆盖扫描终点；上限须大于 0 且不超过 {MAX_CURRENT_A:g} A')
        for name in ['voltage_limit_v', 'max_on_s', 'stable_timeout_s', 'cooldown_s', 'cooldown_min_s']:
            value = getattr(self, name)
            if value is not None and (value < 0 or (name not in {'cooldown_s', 'cooldown_min_s'} and value == 0)):
                raise ValueError(f'{name} 无效')
        if live and any(getattr(self, name) is None for name in ['voltage_limit_v','max_on_s','stable_timeout_s','cooldown_s']):
            raise ValueError('实机限值待定：请明确电压、最大通电、稳定超时和冷却时间')
        if self.voltage_limit_v is not None and self.voltage_limit_v > 360:
            raise ValueError('电压限值超出 IT6524D 范围')
        if self.stable_timeout_s is not None and self.stable_timeout_s <= self.stable_hold_s:
            raise ValueError("稳定超时必须大于稳定保持时间")
        if self.max_on_s is not None and self.stable_timeout_s is not None and self.stable_timeout_s >= self.max_on_s:
            raise ValueError('稳定超时必须小于最大通电时间')
        if self.cooling_mode not in {'fixed','temperature','current','current_temperature'}:
            raise ValueError('冷却模式无效')
        if self.cooling_mode.startswith('current'):
            if live and self.cooldown_min_s is None:
                raise ValueError('电流相关冷却需要明确最短冷却时间')
            if self.cooldown_s is not None and self.cooldown_min_s is not None and self.cooldown_min_s > self.cooldown_s:
                raise ValueError('最短冷却时间不能大于 6 A 基准冷却时间')
        if self.cooling_mode in {'temperature','current_temperature'}:
            if self.temperature_limit_c is None or self.temperature_resume_c is None or self.temperature_resume_c >= self.temperature_limit_c:
                raise ValueError('请设置温度上限和更低的恢复阈值')
        if self.temperature_max_age_s <= 0 or self.temperature_wait_timeout_s <= 0 or self.memory_limit_mb <= 0:
            raise ValueError('温度或内存参数无效')
        if live and self.protection_mode == 'independent' and (not self.inductive_protection_confirmed or not self.independent_cutoff_confirmed or not self.protection_notes.strip()):
            raise ValueError('实机需要确认续流/钳位与独立超时断电保护，并记录保护配置')

    def points(self):
        start, end, step = map(lambda value: Decimal(str(value)), (self.start_a,self.stop_a,self.step_a))
        values = []
        while start <= end:
            values.append(float(start)); start += step
        if len(values) > 601:
            raise ValueError('扫描电流档数过多')
        return values

    def acquisition(self, source: AcquisitionConfig):
        def finite(value):
            if isinstance(value, dict):
                for item in value.values():finite(item)
            elif isinstance(value, (list,tuple)):
                for item in value:finite(item)
            elif isinstance(value, float) and not math.isfinite(value):raise ValueError("采集快照含非有限值")
        finite(source.to_dict())
        config = AcquisitionConfig.from_dict(source.to_dict())
        total = round(config.sample_rate_hz * self.record_duration_us * 1e-6)
        config.pre_trigger_samples = round(total * source.trigger_position_ratio)
        config.post_trigger_samples = total - config.pre_trigger_samples
        config.validate()
        if self.waveform_guard_enabled:
            if not config.awg.enabled:
                raise ValueError('波形保护需要启用 AWG')
            if config.awg.waveform not in {'hann_burst','hann_ramp_hold','lcr_tone'}:
                raise ValueError('当前波形保护支持 Hann burst / ramp hold / LCR tone；其他波形不能自动判定')
            if config.sample_rate_hz < 16*config.awg.frequency_hz:
                raise ValueError('波形形变检测需要每个基波周期至少 16 个采样点')
            for _, amplitude in self.scan_plan(config):
                trial = AcquisitionConfig.from_dict(config.to_dict())
                trial.awg.pk_to_pk_v = amplitude
                trial.validate()
            if self.h2_enabled and (2*config.awg.frequency_hz*(1+self.harmonic_band_fraction) >= config.sample_rate_hz/2):
                raise ValueError('二次谐波滤波带超过奈奎斯特频率')
        if self.filter_enabled and self.filter_high_hz >= config.sample_rate_hz / 2:
            raise ValueError('带通上限必须低于采样率的一半')
        if self.pzt_channel not in config.enabled_channels:
            raise ValueError('PZT 通道未启用')
        start = -config.pre_trigger_samples / config.sample_rate_hz * 1e6
        end = (config.post_trigger_samples - 1) / config.sample_rate_hz * 1e6
        if not start <= self.direct_start_us < self.direct_end_us <= end:
            raise ValueError(f'直达波窗口必须完整位于 {start:g}–{end:g} μs 记录内')
        if (self.direct_end_us-self.direct_start_us)*config.sample_rate_hz*1e-6 < 2:
            raise ValueError('时间窗至少需要两个采样点')
        for channel in [self.excitation_voltage_channel, self.excitation_current_channel]:
            if channel and channel not in config.enabled_channels:
                raise ValueError(f'激励通道 {channel} 未在实时测量快照中启用')
        if self.excitation_voltage_channel or self.excitation_current_channel:
            if not start <= self.excitation_start_us < self.excitation_end_us <= end:
                raise ValueError('激励窗口必须完整位于记录内')
            if (self.excitation_end_us-self.excitation_start_us)*config.sample_rate_hz*1e-6 < 2:
                raise ValueError('激励窗口至少需要两个采样点')
        if self.waveform_guard_enabled:
            from ..waveforms import normalized_waveform
            _, repetition = normalized_waveform(config.awg)
            if (self.excitation_end_us-self.excitation_start_us)*1e-6 < 1/repetition*1.01:
                raise ValueError('激励窗需完整覆盖 burst，并保留少量起止余量')
            if (self.excitation_end_us-self.excitation_start_us)*config.sample_rate_hz*1e-6 < 40:
                raise ValueError('波形保护激励窗至少需要 40 个样本')
            if self.h2_enabled and (self.direct_end_us-self.direct_start_us)*config.sample_rate_hz*1e-6 < 40:
                raise ValueError('二次谐波评价窗至少需要 40 个样本')
        budget = total * (len(config.enabled_channels)+1) * 8 * self.repeats
        if budget > self.memory_limit_mb * 1024**2:
            raise ValueError('单档原始数据超过内存预算')
        return config

    def scan_plan(self, acquisition):
        currents = self.points()
        if self.amplitude_mode == 'snapshot':
            amplitudes = [acquisition.awg.pk_to_pk_v]
        elif self.amplitude_mode == 'fixed':
            amplitudes = [self.amplitude_fixed_vpp]
        else:
            start, end, step = map(lambda v: Decimal(str(v)), (self.amplitude_start_vpp,self.amplitude_stop_vpp,self.amplitude_step_vpp))
            amplitudes = []
            while start <= end:
                amplitudes.append(float(start));start += step
        if len(currents)*len(amplitudes)>601:
            raise ValueError('偏置 × 幅值组合超过 601 点')
        return ([(current,amplitude) for current in currents for amplitude in amplitudes] if self.amplitude_order=='bias_outer'
                else [(current,amplitude) for amplitude in amplitudes for current in currents])

    def effective_limits(self, simulate):
        # These values are explicitly simulation-only, never fallbacks for hardware.
        return {'voltage_limit_v': self.voltage_limit_v if self.voltage_limit_v is not None else (40 if simulate else None),
                'max_on_s': self.max_on_s if self.max_on_s is not None else (3 if simulate else None),
                'stable_timeout_s': self.stable_timeout_s if self.stable_timeout_s is not None else (1 if simulate else None),
                'cooldown_s': self.cooldown_s if self.cooldown_s is not None else (0.1 if simulate else None),
                'cooldown_min_s': self.cooldown_min_s if self.cooldown_min_s is not None else (0 if simulate else None)}

    def cooldown_for(self, current_a, simulate=False):
        """User calibrated I² schedule, not a coil temperature/safety prediction."""
        if not math.isfinite(current_a) or current_a < 0:
            raise ValueError('冷却电流必须为有限非负值')
        limits = self.effective_limits(simulate)
        if self.cooling_mode.startswith('current'):
            if current_a == 0:
                return 0.0
            if limits['cooldown_s'] is None or limits['cooldown_min_s'] is None:
                raise ValueError('请填写 6 A 基准冷却和最短冷却时间')
            return max(limits['cooldown_min_s'], limits['cooldown_s'] * (current_a / 6.0) ** 2)
        return limits['cooldown_s']
