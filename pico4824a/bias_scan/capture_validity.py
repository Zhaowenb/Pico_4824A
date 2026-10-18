"""Basic capture validity, separate from optional amplifier waveform protection.

No decision is based on PZT amplitude or neighbouring bias/amplitude points.
ps4000a block readiness alone does not prove a hardware trigger occurred.
"""
import math
import numpy as np
from ..waveforms import BURST_WAVEFORMS
from ..config import RANGE_VOLTS


def assess_capture(capture, config):
    result = {'valid':True, 'status':'OK', 'reason':'', 'monitor_snr_db':{}}
    def fail(code, reason):
        result.update(valid=False, status=code, reason=reason)
        return result
    t = np.asarray(capture.time_s)
    if len(t)<2 or not np.all(np.isfinite(t)) or np.any(np.diff(t)<=0):
        return fail('INVALID_CAPTURE', '采集时间轴缺失或异常')
    if len(t) != capture.config.total_samples:
        return fail('INCOMPLETE_CAPTURE', '采集返回点数不足，未完成整段捕获')
    if capture.overflow_channels:
        return fail('ADC_OVERFLOW', '采集输入溢出：'+','.join(capture.overflow_channels))
    info = getattr(capture, 'capture_info', {})
    if info.get('auto_triggered') is True or info.get('valid') is False:
        return fail('CAPTURE_FAILED', '采集元数据标记触发或捕获失败')
    acquisition = capture.config
    trigger = acquisition.trigger
    raw_trigger = capture.volts.get(trigger.source)
    # Conservative corroboration: elapsed time alone is never a trigger verdict.
    if trigger.enabled and trigger.auto_trigger_ms>0 and raw_trigger is not None:
        waited = info.get('ready_wait_s')
        values = np.asarray(raw_trigger)
        if isinstance(waited, (float,int)) and math.isfinite(waited) and waited >= trigger.auto_trigger_ms/1000 and np.all(np.isfinite(values)) and len(values):
            tolerance = RANGE_VOLTS[acquisition.channels[trigger.source].range]/2047*2
            unreachable = (np.max(values)<trigger.threshold_v-tolerance if trigger.direction in {'rising','above'}
                           else np.min(values)>trigger.threshold_v+tolerance if trigger.direction in {'falling','below'}
                           else np.min(values)>trigger.threshold_v+tolerance or np.max(values)<trigger.threshold_v-tolerance)
            if unreachable:
                return fail('TRIGGER_MISSED', '疑似自动触发：等待已达自动触发时限，原始触发通道未达到阈值')
    channels = list(dict.fromkeys(c for c in [config.excitation_voltage_channel,config.excitation_current_channel] if c))
    if not channels or not acquisition.awg.enabled or acquisition.awg.waveform not in BURST_WAVEFORMS:
        return result
    time_us = t*1e6
    active = (time_us>=config.excitation_start_us)&(time_us<=config.excitation_end_us)
    if active.sum()<8:
        result.update(status='UNVERIFIED', reason='激励窗口不足 8 个样本，未据此排除采集')
        return result
    # Take quiet samples well outside the burst window. DC offsets are removed.
    margin = max(config.excitation_end_us-config.excitation_start_us, 1.0)
    quiet = (time_us<config.excitation_start_us-margin)|(time_us>config.excitation_end_us+margin)
    if quiet.sum()<16:
        result['status']='UNVERIFIED'
        result['reason']='记录中没有足够静默区；未据此排除采集'
        return result
    detected = []
    for channel in channels:
        values = capture.volts.get(channel)
        if values is None or len(values)!=len(t) or not np.all(np.isfinite(values)):
            return fail('INVALID_MONITOR', f'激励 {channel} 原始采集缺失或非有限')
        pulse = np.asarray(values)[active]
        baseline = np.asarray(values)[quiet]
        pulse_rms = float(np.sqrt(np.mean((pulse-np.mean(pulse))**2)))
        noise_rms = float(np.sqrt(np.mean((baseline-np.mean(baseline))**2)))
        ratio = pulse_rms/max(noise_rms,1e-12)
        result['monitor_snr_db'][channel] = float(20*np.log10(max(1e-12,min(ratio,1e12))))
        detected.append(ratio>=3)
    if detected and not any(detected):
        return fail('EXCITATION_NOT_CAPTURED', '激励监测窗内未捕获到可辨识波包（所有监测通道均不足静默区 RMS 的 3 倍）；疑似漏触发／激励缺失')
    return result
