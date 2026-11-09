"""Repeated live captures; reuse block capture and raw storage without averaging."""
from dataclasses import dataclass
import json
import math
import time
from .config import AcquisitionConfig
from .storage import save_npz
from .storage_naming import session_directory


@dataclass(frozen=True)
class RepeatCaptureConfig:
    enabled: bool = False
    count: int = 5
    echo_wait_s: float = .1

    @classmethod
    def from_dict(cls, raw=None):
        if raw is None:
            return cls()
        if not isinstance(raw, dict) or not isinstance(raw.get('enabled', False), bool):
            raise ValueError('重复测量配置无效')
        if not raw.get('enabled', False):
            return cls()
        try:
            count = float(raw.get('count', 5))
            wait = float(raw.get('echo_wait_s', .1))
        except (ValueError, TypeError):
            raise ValueError('测量次数和回波等待时间必须是数字') from None
        if isinstance(raw.get('count'), bool) or not math.isfinite(count) or not count.is_integer() or not 1<=count<=10000:
            raise ValueError('重复测量次数须为 1–10000 的整数')
        if isinstance(raw.get('echo_wait_s'), bool) or not math.isfinite(wait) or not 0<=wait<=3600:
            raise ValueError('回波等待时间须在 0–3600 s 内')
        return cls(True, int(count), wait)


def run_capture_sequence(device, config, repeat, root, stop_event, on_capture, on_progress):
    folder = session_directory(root, 'capture', f'重复{repeat.count}次', simulated=device.simulate)
    rows = []
    result = dict(status='running', completed=0, total=repeat.count, echo_wait_s=repeat.echo_wait_s,
                  output_dir=str(folder), runs=rows, reason='')
    (folder/'config.json').write_text(json.dumps(dict(acquisition=config.to_dict(),
        repetition=dict(count=repeat.count,echo_wait_s=repeat.echo_wait_s,
                        interval_definition='capture_return_to_next_capture_start')), ensure_ascii=False, indent=2),encoding='utf-8')
    def publish(phase, remaining=0):
        on_progress(dict(phase=phase,completed=len(rows),total=repeat.count,
                         wait_remaining_s=remaining,output_dir=str(folder),echo_wait_s=repeat.echo_wait_s))
    try:
        for index in range(1,repeat.count+1):
            if stop_event.is_set():break
            publish('capturing')
            started=time.time()
            capture=device.capture(AcquisitionConfig.from_dict(config.to_dict()))
            returned=time.monotonic()
            ended=time.time()
            capture.capture_info['repeat_measurement']=dict(index=index,total=repeat.count,echo_wait_s=repeat.echo_wait_s)
            channels='-'.join(capture.volts)
            stem=folder/f'第{index:04d}次__CH-{channels}__原始波形'
            save_npz(capture,stem.with_suffix('.npz'))
            rows.append(dict(index=index,file=stem.name+'.npz',capture_start=started,capture_end=ended,
                             overflow_channels=list(capture.overflow_channels)))
            result['completed']=len(rows)
            on_capture(capture,stem)
            publish('saved')
            # Storage may consume part of the quiet interval; never shorten it.
            if index<repeat.count and not stop_event.is_set():
                deadline=returned+repeat.echo_wait_s
                while not stop_event.is_set():
                    remaining=deadline-time.monotonic()
                    if remaining<=0:break
                    publish('waiting',remaining)
                    stop_event.wait(min(.1,remaining))
        result['status']='stopped' if stop_event.is_set() else 'complete'
    except Exception as exc:
        result.update(status='stopped' if stop_event.is_set() else 'error',reason=str(exc))
        raise
    finally:
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        publish(result['status'])
    return result
