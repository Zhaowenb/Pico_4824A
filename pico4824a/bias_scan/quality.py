"""OFF-only waveform diagnosis. Reuses the project's AWG and harmonic algorithms."""
import math
from types import SimpleNamespace
import numpy as np
from ..waveforms import normalized_waveform
from ..analysis import fft_bandpass
from ..lcr_linearity import _harmonics
from ..big_signal_lcr import _suggest_monitor_ranges
from ..config import RANGE_VOLTS


def suggest_ranges(capture, acquisition, config):
    changes, error = _suggest_monitor_ranges(capture, acquisition, SimpleNamespace(
        voltage_channel=config.excitation_voltage_channel,current_channel=config.excitation_current_channel))
    rx, rx_error = _suggest_monitor_ranges(capture, acquisition, SimpleNamespace(
        voltage_channel=config.pzt_channel,current_channel=config.pzt_channel))
    changes.update(rx)
    return changes, error or rx_error


def _flat_top(values, frequency, sample_rate):
    peak=float(np.max(np.abs(values)))
    if peak<=0:return False
    flat=(np.abs(np.diff(values)) < peak*.002)&(np.minimum(np.abs(values[:-1]),np.abs(values[1:]))>peak*.8)
    needed=max(3,int(math.ceil(sample_rate/frequency*.08)))
    if len(flat)<needed:return False
    # Consecutive equal samples near a peak distinguish a plateau from a smooth sine extremum.
    return bool(np.any(np.convolve(flat.astype(int),np.ones(needed,dtype=int),'valid')>=needed))


def _template_residual(time, values, awg):
    template,repetition=normalized_waveform(awg)
    duration=1/repetition
    if time[-1]-time[0]<duration*.98:
        raise ValueError('激励时间窗未完整覆盖 AWG burst')
    n=len(template);spectrum=np.fft.fft(template);multiplier=np.zeros(n)
    multiplier[0]=1;multiplier[1:(n+1)//2]=2
    if n%2==0:multiplier[n//2]=1
    quadrature=np.fft.ifft(spectrum*multiplier).imag
    sample_rate=1/float(np.median(np.diff(time)))
    stride=max(1,min(int(math.ceil(len(time)/4000)),int(sample_rate/(16*awg.frequency_hz))))
    t=time[::stride];v=values[::stride];denominator=float(np.linalg.norm(v-np.mean(v)))
    if denominator<=1e-12:raise ValueError('激励信号过小或恒定，无法判定')
    # Search the measured window; never assume TX starts exactly at trigger t=0.
    starts=np.linspace(t[0],max(t[0],t[-1]-duration),min(161,max(2,int((t[-1]-t[0])*awg.frequency_hz*12))))
    source=np.arange(n)*duration/n;best=1.
    for start in starts:
        u=t-start
        x=np.interp(u,source,template,left=0,right=0);q=np.interp(u,source,quadrature,left=0,right=0)
        design=np.column_stack([np.ones(len(t)),x,q]);coefficients,*_=np.linalg.lstsq(design,v,rcond=None)
        best=min(best,float(np.linalg.norm(v-design@coefficients)/denominator))
    return best


def assess(capture, acquisition, config, reference=None):
    output={'status':'OK','reason':'','excitation_ipp_a':None,'receiver_h2_h1_pct':None,'receiver_h1_rms_v':None,'receiver_h2_rms_v':None}
    def fail(code, reason):output.update(status=code,reason=reason);return output
    t=np.asarray(capture.time_s);mask=(t*1e6>=config.excitation_start_us)&(t*1e6<=config.excitation_end_us)
    if len(t)<2 or not np.all(np.isfinite(t)) or np.any(np.diff(t)<=0):return fail('INVALID','原始时间轴异常')
    if np.count_nonzero(mask)<40:return fail('INVALID','激励窗样本不足，无法判定波形')
    for role,channel,scale in [('voltage',config.excitation_voltage_channel,config.excitation_voltage_scale),('current',config.excitation_current_channel,config.excitation_current_scale)]:
        raw=capture.volts.get(channel)
        if raw is None or len(raw)!=len(t) or not np.all(np.isfinite(raw)):return fail('INVALID',f'激励 {role} 原始数据异常')
        if channel in capture.overflow_channels or np.max(np.abs(raw))>=.98*RANGE_VOLTS[capture.config.channels[channel].range]:
            return fail('ADC_RANGE',f'激励 {channel} ADC 溢出/接近满量程，须断电后重采')
        segment=raw[mask];vpp=float(np.ptp(segment))*scale
        if not math.isfinite(vpp):return fail('INVALID','探头系数换算结果非有限')
        output['excitation_'+role+'_vpp']=vpp
        if role=='current':
            output['excitation_ipp_a']=vpp
            if vpp>config.excitation_ipp_limit_a:return fail('CURRENT_LIMIT',f'激励 Ipp {vpp:.4g} A 超过 {config.excitation_ipp_limit_a:g} A')
        if float(np.sqrt(np.mean((segment-np.mean(segment))**2)))<config.monitor_min_rms_v:
            return fail('SIGNAL_LOST',f'激励 {channel} 未达到有效幅值：疑似断裂、未触发或功放跳闸')
        if reference:
            baseline=reference.get('excitation_'+role+'_vpp')
            if baseline and vpp/baseline<config.trip_drop_ratio:return fail('SUSPECT_TRIP',f'激励 {channel} 相对本点前次剩余 {vpp/baseline:.1%}，疑似跳闸')
            if baseline and abs(vpp/baseline-1)>config.repeat_change_fraction:return fail('UNSTABLE',f'激励 {channel} 重复幅值变化 {abs(vpp/baseline-1):.1%}')
        if _flat_top(segment,acquisition.awg.frequency_hz,capture.actual_sample_rate_hz):
            return fail('AMPLIFIER_CLIP',f'激励 {channel} 原始波形疑似削顶；ADC 未满量程，禁止靠扩大量程绕过')
        try:residual=_template_residual(t[mask],segment,acquisition.awg)
        except ValueError as exc:return fail('INVALID',str(exc))
        output[role+'_shape_residual']=residual
        if residual>config.shape_residual_limit:return fail('SHAPE_FAULT',f'激励 {channel} 与实际 AWG 模板偏差 {residual:.1%}，疑似形变/突然断裂')
    channel=config.pzt_channel;raw=capture.volts.get(channel)
    if raw is None or not np.all(np.isfinite(raw)):return fail('INVALID','PZT 原始数据异常')
    if channel in capture.overflow_channels or np.max(np.abs(raw))>=.98*RANGE_VOLTS[capture.config.channels[channel].range]:
        return fail('ADC_RANGE','PZT ADC 超限，须断电后调整量程重采')
    if config.h2_enabled:
        try:
            frequency=acquisition.awg.frequency_hz;fs=capture.actual_sample_rate_hz
            filtered=np.zeros(len(raw))
            for center in [frequency,2*frequency]:
                width=center*config.harmonic_band_fraction
                if center+width>=fs/2:raise ValueError('实际采样率不能覆盖二次谐波滤波带')
                filtered+=fft_bandpass(raw,fs,center-width,center+width,width*.25)
            window=(t*1e6>=config.direct_start_us)&(t*1e6<=config.direct_end_us)
            fit=_harmonics(t[window],filtered[window],frequency,2)
            output.update(receiver_h2_h1_pct=fit['h2_h1_pct'],receiver_h1_rms_v=fit['harmonics_rms'][0],receiver_h2_rms_v=fit['harmonics_rms'][1])
            if fit['h2_h1_pct']>config.h2_limit_pct:return fail('H2_LIMIT',f'滤波后直达波 H2/H1 {fit["h2_h1_pct"]:.3g}% 超过 {config.h2_limit_pct:g}%')
        except (ValueError,KeyError,np.linalg.LinAlgError) as exc:return fail('H2_INVALID','二次谐波无法可靠评价：'+str(exc))
    return output
