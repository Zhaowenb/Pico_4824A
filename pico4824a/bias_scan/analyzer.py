import math
import numpy as np


class BiasScanAnalyzer:
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
        window=values[(time_us>=config.direct_start_us)&(time_us<=config.direct_end_us)]
        if len(window)<2 or not np.all(np.isfinite(window)):
            return None,'窗口样本不足或包含非有限值'
        return float(np.ptp(window)),''

    @staticmethod
    def summary(target, rows, repeats, eligible=True):
        valid=[row for row in rows if row['valid']]
        currents=[row['actual_current_a'] for row in rows if row.get('actual_current_a') is not None]
        values=[row['vpp_v'] for row in valid]
        return {'target_a':target,'captured_count':len(rows),'valid_count':len(valid),
                'eligible':eligible and len(rows)==repeats and len(valid)==repeats,
                'actual_current_mean_a':float(np.mean(currents)) if currents else None,
                'actual_current_sd_a':float(np.std(currents,ddof=1)) if len(currents)>1 else None,
                'vpp_mean_v':float(np.mean(values)) if values else None,
                'vpp_sd_v':float(np.std(values,ddof=1)) if len(values)>1 else None}

    @staticmethod
    def best(rows):
        valid=[row for row in rows if row['eligible'] and row['vpp_mean_v'] is not None]
        if not valid:return {'best_current_a':None,'ties_a':[]}
        maximum=max(row['vpp_mean_v'] for row in valid)
        ties=sorted(row['target_a'] for row in valid if row['vpp_mean_v']==maximum)
        return {'best_current_a':ties[0],'best_vpp_mean_v':maximum,'ties_a':ties}
