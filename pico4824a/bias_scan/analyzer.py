import math
import numpy as np
from ..analysis import fft_bandpass


class BiasScanAnalyzer:
    @staticmethod
    def evaluation_signal(result, config):
        values = result.volts[config.pzt_channel]
        if config.filter_enabled:
            return fft_bandpass(values, result.actual_sample_rate_hz, config.filter_low_hz,
                                config.filter_high_hz, config.filter_transition_hz)
        return values

    @staticmethod
    def vpp(result, config):
        if result.overflow_channels:
            return None, '采集输入溢出：'+','.join(result.overflow_channels)
        values=result.volts.get(config.pzt_channel)
        time_us=result.time_s*1e6
        if values is None or len(values)!=len(time_us) or len(time_us)<2:
            return None,'缺少有效 PZT 数据'
        if not np.all(np.isfinite(values)):
            return None,'原始 PZT 波形包含非有限值'
        if not math.isfinite(result.actual_sample_rate_hz) or result.actual_sample_rate_hz<=0:
            return None,'采样率元数据无效'
        if not np.all(np.isfinite(time_us)) or np.any(np.diff(time_us)<=0):
            return None,'时间轴无效'
        if config.direct_start_us < time_us[0]-1e-8 or config.direct_end_us > time_us[-1]+1e-8:
            return None,'直达波窗口不完整'
        try:
            evaluated = BiasScanAnalyzer.evaluation_signal(result, config)
        except ValueError as exc:
            return None, '滤波评价失败：'+str(exc)
        window=evaluated[(time_us>=config.direct_start_us)&(time_us<=config.direct_end_us)]
        if len(window)<2 or not np.all(np.isfinite(window)):
            return None,'窗口样本不足或包含非有限值'
        return float(np.ptp(window)),''

    @staticmethod
    def aggregate(values, method='mean'):
        values = sorted(float(v) for v in values if v is not None and math.isfinite(v))
        if method == 'trimmed_mean':
            values = values[1:-1] if len(values) >= 3 else []
        if not values:
            return None, None, 0
        center = np.median(values) if method == 'median' else np.mean(values)
        return float(center), float(np.std(values, ddof=1)) if len(values)>1 else None, len(values)

    @staticmethod
    def excitation(result, config):
        metrics = {}
        for role, unit in [('voltage', 'v'), ('current', 'a')]:
            channel = getattr(config, 'excitation_'+role+'_channel')
            key = 'excitation_'+role+'_vpp_'+unit
            metrics[key] = None
            metrics['excitation_'+role+'_reason'] = ''
            if not channel:
                continue
            values = result.volts.get(channel)
            t = result.time_s * 1e6
            reason = ''
            if channel in result.overflow_channels:
                reason = '激励通道输入溢出'
            elif values is None or len(values)!=len(t) or len(t)<2 or not np.all(np.isfinite(values)):
                reason = '激励原始数据无效'
            elif not np.all(np.isfinite(t)) or np.any(np.diff(t)<=0):
                reason = '时间轴无效'
            elif config.excitation_start_us < t[0]-1e-8 or config.excitation_end_us > t[-1]+1e-8:
                reason = '激励窗口不完整'
            else:
                window = values[(t>=config.excitation_start_us)&(t<=config.excitation_end_us)]
                if len(window)<2:
                    reason = '激励窗口样本不足'
                else:
                    converted = float(np.ptp(window))*getattr(config, 'excitation_'+role+'_scale')
                    if math.isfinite(converted):
                        metrics[key] = converted
                    else:
                        reason = '激励换算结果非有限值'
            metrics['excitation_'+role+'_reason'] = reason
        return metrics

    @staticmethod
    def summary(target, rows, repeats, eligible=True, method='mean'):
        valid=[row for row in rows if row['valid']]
        currents=[row['actual_current_a'] for row in rows if row.get('actual_current_a') is not None]
        values=[row['vpp_v'] for row in valid]
        center, sd, count = BiasScanAnalyzer.aggregate(values, method)
        summary = {'target_a':target,'captured_count':len(rows),'valid_count':len(valid),
                'eligible':eligible and len(rows)==repeats and len(valid)==repeats,
                'actual_current_mean_a':float(np.mean(currents)) if currents else None,
                'actual_current_sd_a':float(np.std(currents,ddof=1)) if len(currents)>1 else None,
                'aggregation':method, 'statistics_count':count,
                'vpp_mean_v':center, 'vpp_sd_v':sd}
        for role, unit in [('voltage','v'), ('current','a')]:
            key = 'excitation_'+role
            values = [r.get(key+'_vpp_'+unit) for r in rows]
            center, sd, count = BiasScanAnalyzer.aggregate(values, method)
            summary.update({key+'_mean_'+unit:center, key+'_sd_'+unit:sd, key+'_statistics_count':count,
                            key+'_valid_count':sum(v is not None for v in values)})
        return summary

    @staticmethod
    def best(rows):
        valid=[row for row in rows if row['eligible'] and row['vpp_mean_v'] is not None]
        if not valid:return {'best_current_a':None,'ties_a':[]}
        maximum=max(row['vpp_mean_v'] for row in valid)
        ties=sorted(row['target_a'] for row in valid if row['vpp_mean_v']==maximum)
        return {'best_current_a':ties[0],'best_vpp_mean_v':maximum,'ties_a':ties}
