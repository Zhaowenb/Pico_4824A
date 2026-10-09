from dataclasses import asdict
from pathlib import Path
import csv
import json
import math
import shutil
import threading
import time

from .analyzer import BiasScanAnalyzer
from .safety import SafetyProtection, SimulationProtection, TemperatureProvider
from ..storage import save_npz
from ..storage_naming import session_directory, preferences


class BiasScanController:
    """One job, immutable configuration; no analysis or waveform I/O while powered."""
    def __init__(self, config, acquisition, power, adapter, output_root, simulate=False,
                 stop_event=None, progress=None, protection=None, temperature=None):
        self.config, self.simulate = config, simulate
        self.scan_name = config.scan_name or preferences()["bias"]
        config.validate(live=not simulate)
        self.acquisition = config.acquisition(acquisition)
        self.power, self.adapter = power, adapter
        self.output_root = Path(output_root).resolve()
        target = Path(__file__).resolve().parents[2]
        if not self.output_root.is_relative_to(target):
            raise ValueError('偏置扫描输出必须位于 TARGET_ROOT')
        self.stop_event = stop_event or threading.Event()
        self.progress = progress or (lambda state: None)
        self.protection = protection or (SimulationProtection() if simulate else SafetyProtection())
        self.temperature = temperature or TemperatureProvider()
        self.limits = config.effective_limits(simulate)
        self.events, self.rows, self.summaries = [], [], []
        self.result = {'status':'running','simulated':simulate,'summary':self.summaries,'runs':self.rows,
                       'events':self.events,'output_state':'unknown','reason':'','revision':0}
        self._off_lock = threading.Lock()
        self._fault_lock = threading.Lock()
        self._fault = None
        self._last_on_duration = 0.0
        self._on_at = None
        self._off_at = None
        self._telemetry = None
        self._point_done = threading.Event()
        self._threads = []
        self._unknown_latched = False
        self.folder = None
        self._last_progress = {}

    def event(self, name, **values):
        self.events.append({'event':name,'monotonic_time':time.monotonic(),'wall_time':time.time(),**values})

    def publish(self, phase, **values):
        self._last_progress.update(values)
        self.progress({'phase':phase,'output_state':self.power.output_state,
                       'on_elapsed_s':max(0,time.monotonic()-self._on_at) if self._on_at else self._last_on_duration,
                       'actual_current_a':self._telemetry.get('current_a') if self._telemetry else None,
                       'completed_points':len(self.summaries),'total_points':len(self.config.points()),**self._last_progress})

    def off(self):
        with self._off_lock:
            self.event('off_requested')
            try:
                self.power.output_off()
                if self._on_at is not None or self._off_at is None:self._off_at = time.monotonic()
                if self._on_at is not None:self._last_on_duration=self._off_at-self._on_at
                self.event('off_confirmed')
                self.result['output_state'] = 'off'
            except BaseException as exc:
                self._unknown_latched = True
                self.result['output_state'] = 'unknown'
                self.event('off_failed', reason=str(exc))
                try:self.protection.trip('输出状态未知')
                except Exception as guard_exc:self.event('protection_failed',reason=str(guard_exc))
                raise RuntimeError('输出状态未知：'+str(exc)) from exc
            finally:
                self._on_at = None

    def trip(self, reason):
        with self._fault_lock:
            if self._fault is not None:return
            self._fault = reason
        self.event('safety_trip', reason=reason)
        try:self.off()
        except Exception:pass
        try:self.protection.trip(reason)
        except Exception as exc:self.event('protection_failed',reason=str(exc))
        # The bias output command has priority over stopping Pico.
        try:self.adapter.stop()
        except Exception as exc:self.event('pico_stop_failed',reason=str(exc))

    def stop(self):
        self.stop_event.set()
        self.trip('用户停止')

    def check(self):
        if self._fault:raise RuntimeError(self._fault)
        if self.stop_event.is_set():raise RuntimeError('用户停止')
        if self._unknown_latched:raise RuntimeError('输出状态未知，禁止继续')

    def temperature_reading(self):
        r = self.temperature.read()
        age = time.monotonic()-r.monotonic_time
        if not math.isfinite(r.celsius) or not math.isfinite(age) or not 0 <= age <= self.config.temperature_max_age_s:
            raise RuntimeError('温度缺失、过期或异常')
        return r.celsius

    def _deadline(self, deadline, done):
        while not done.wait(.01):
            if self.stop_event.is_set():self.trip('用户停止');return
            if time.monotonic() >= deadline:self.trip('单档最大通电时间超限');return

    def _monitor(self, done):
        while not done.is_set():
            query_start = time.monotonic()
            try:
                telemetry = self.power.read_actual()
                if done.is_set():return
                self._telemetry = telemetry
                self.event('telemetry',**telemetry)
                if not 0 <= telemetry['current_a'] <= self.config.actual_current_limit_a:
                    raise RuntimeError('实际电流超过安全上限或出现负值')
                if telemetry['voltage_v'] < 0 or telemetry['voltage_v'] > self.limits['voltage_limit_v']+.02:
                    raise RuntimeError('实际电压超过合规限值')
                if self.config.cooling_mode=='temperature' and self.temperature_reading() >= self.config.temperature_limit_c:
                    raise RuntimeError('温度超过安全阈值')
            except Exception as exc:
                if not done.is_set() and not self._fault:self.trip(str(exc))
                return
            done.wait(max(0, query_start+self.config.poll_s-time.monotonic()))

    def _stable(self, target):
        deadline=time.monotonic()+self.limits['stable_timeout_s']; stable_since=None
        while True:
            self.check(); now=time.monotonic(); telemetry=self._telemetry
            if telemetry and now-telemetry['monotonic_time'] <= max(.5,self.config.poll_s*4):
                if abs(telemetry['current_a']-target)<=max(self.config.stable_abs_a,target*self.config.stable_rel):
                    stable_since=stable_since or telemetry['monotonic_time']
                    # Wall time alone cannot prove stability from one reading.
                    if telemetry['monotonic_time']-stable_since>=self.config.stable_hold_s:return
                else:stable_since=None
            else:stable_since=None
            if now>=deadline:raise RuntimeError('实际电流无法在稳定超时内稳定')
            self.stop_event.wait(.01)

    def _cool(self):
        self.publish('cooling')
        deadline=self._off_at+self.limits['cooldown_s']
        while time.monotonic()<deadline:
            self.check();self.stop_event.wait(min(.05,max(0,deadline-time.monotonic())))
        if self.config.cooling_mode=='temperature':
            deadline=time.monotonic()+self.config.temperature_wait_timeout_s
            while self.temperature_reading()>self.config.temperature_resume_c:
                self.check()
                if time.monotonic()>=deadline:raise RuntimeError('温度未恢复，扫描中止')
                self.stop_event.wait(self.config.poll_s)

    @staticmethod
    def _csv(path, rows):
        if not rows:path.write_text('',encoding='utf-8');return
        fields=list(dict.fromkeys(key for row in rows for key in row))
        with path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)

    def persist(self):
        self.event('save_task')
        self._csv(self.folder/'runs.csv',self.rows)
        self._csv(self.folder/'summary.csv',self.summaries)
        self.result.update(BiasScanAnalyzer.best(self.summaries))
        (self.folder/'result.json').write_text(json.dumps(self.result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

    def run(self):
        try:
            if not self.protection.ready():raise RuntimeError('尚未接入独立超时断电保护适配器；实机启动被锁定')
            identity=self.power.connect();self.off()
            self.adapter.prepare()
            self.check()
            if self.config.cooling_mode=='temperature' and self.temperature_reading()>=self.config.temperature_limit_c:
                raise RuntimeError('预检温度超过阈值')
            self.folder=session_directory(self.output_root, 'bias',
                details=f'{self.config.start_a:g}-{self.config.stop_a:g}A_步进{self.config.step_a:g}A_{self.config.repeats}次',
                name=self.scan_name, simulated=self.simulate)
            required=self.acquisition.total_samples*(len(self.acquisition.enabled_channels)+1)*8*self.config.repeats*len(self.config.points())
            if shutil.disk_usage(self.folder).free < required+16*1024**2:raise RuntimeError('输出磁盘空间不足')
            self.result.update(output_dir=str(self.folder),device_identity=identity,
                               configuration=asdict(self.config),acquisition=self.acquisition.to_dict(),effective_limits=self.limits)
            (self.folder/'config.json').write_text(json.dumps({k:self.result[k] for k in ['configuration','acquisition','device_identity','simulated','effective_limits']},ensure_ascii=False,indent=2),encoding='utf-8')
            (self.folder/'文件说明.md').write_text(
                '# 偏置电流扫描数据\n\n'
                'config.json：扫描参数、采集快照、电源身份与安全限值。\n\n'
                'summary.csv：逐档 Vpp 均值、样本标准差、有效性和通电时长。\n\n'
                'runs.csv：逐次采集、实际电流、时间与原始文件相对路径。\n\n'
                'result.json：任务状态、最佳已测电流、安全事件与完整统计。\n\n'
                '每个“电流…A”文件夹：本档逐次 NPZ 原始波形、runs.csv 和 summary.json。\n\n'
                '0 A 是断电基线。仿真文件不是实机测量。正常流程先确认关闭输出，再写入波形、分析和保存；异常输出状态详见 result.json。\n',
                encoding='utf-8')
            self.event('preflight_complete')
            for index,target in enumerate(self.config.points()):
                self.check();self._fault=None;self._telemetry=None;self._point_done=threading.Event();self._threads=[]
                captured=[];error=None;batch_start=None;on_start=None;self._last_on_duration=0
                self.publish('setting',point=index,target_a=target,repeat=0)
                try:
                    if target==0:
                        self.off();self._telemetry=self.power.read_off_actual()
                        if not math.isfinite(self._telemetry['current_a']) or abs(self._telemetry['current_a'])>self.config.stable_abs_a:raise RuntimeError('断电基线电流异常')
                    else:
                        self.power.configure(self.limits['voltage_limit_v'],target)
                        self.protection.arm(self.limits['max_on_s'])
                        self.check()
                        self._on_at=on_start=time.monotonic()
                        self._threads=[threading.Thread(target=self._deadline,args=(on_start+self.limits['max_on_s'],self._point_done),daemon=True)]
                        self._threads[0].start()
                        self.power.output_on();self.event('on',target_a=target)
                        monitor=threading.Thread(target=self._monitor,args=(self._point_done,),daemon=True);self._threads.append(monitor);monitor.start()
                        self.publish('stabilizing',point=index,target_a=target,repeat=0)
                        self._stable(target)
                    batch_start=time.monotonic();previous=None
                    for repeat in range(self.config.repeats):
                        if previous is not None:
                            while not self.stop_event.is_set() and time.monotonic()<previous+self.config.interval_s:
                                self.stop_event.wait(max(0,previous+self.config.interval_s-time.monotonic()))
                        self.check()
                        remaining=(on_start+self.limits['max_on_s']-time.monotonic()) if on_start else self.acquisition.capture_timeout_s
                        if remaining<=0:raise RuntimeError('单档最大通电时间超限')
                        self.publish('capturing',point=index,target_a=target,repeat=repeat+1)
                        started=previous=time.monotonic();telemetry=dict(self._telemetry)
                        self.event('capture_start',point=index,repeat=repeat+1)
                        result=self.adapter.capture(self.acquisition,remaining)
                        ended=time.monotonic()
                        # No reads, callbacks, analysis, compression or disk access here.
                        if repeat==self.config.repeats-1:
                            self._point_done.set()
                            try:self.off()
                            finally:captured.append((result,telemetry,started,ended))
                        else:captured.append((result,telemetry,started,ended))
                        self.event('capture_return',point=index,repeat=repeat+1,returned_at=ended)
                        self.check()
                except BaseException as exc:
                    error=RuntimeError(self._fault) if self._fault else exc
                finally:
                    # Signal telemetry to leave before OFF; no subsequent normal queries.
                    self._point_done.set()
                    try:self.off()
                    except Exception as exc:error=exc
                    if error is not None or self._fault:
                        try:self.adapter.stop()
                        except Exception as exc:self.event("pico_stop_failed",reason=str(exc))
                    for thread in self._threads:thread.join(timeout=.8)
                    if any(thread.is_alive() for thread in self._threads):
                        self.result["instrument_cleanup_pending"]=True
                        error=RuntimeError("安全监控或 Pico 停止未返回，仪器占用保持锁定")
                    try:
                        if not self._unknown_latched:self.protection.disarm()
                    except Exception as exc:error=exc
                try:
                    self.event('analyze',point=index)
                    rows=[]
                    point_folder = self.folder / (f'{index:02d}_电流{target:.3f}A' + ('_断电基线' if target == 0 else ''))
                    point_folder.mkdir(exist_ok=True)
                    for repeat,(result,telemetry,started,ended) in enumerate(captured):
                        value,reason=BiasScanAnalyzer.vpp(result,self.config)
                        row={'point':index,'repeat':repeat+1,'target_a':target,
                             'actual_current_a':telemetry['current_a'],'telemetry_monotonic_time':telemetry['monotonic_time'],
                             'capture_start':started,'capture_end':ended,'capture_duration_s':ended-started,'telemetry_wall_time':telemetry.get('wall_time'),
                             'vpp_v':value,'valid':value is not None,'invalid_reason':reason,
                             'file':(point_folder / f'重复{repeat+1:02d}__PZT-{self.config.pzt_channel}__原始波形.npz').relative_to(self.folder).as_posix()}
                        self.event('save_waveform',point=index,repeat=repeat+1)
                        save_npz(result,self.folder/row['file']);rows.append(row);self.rows.append(row)
                    summary=BiasScanAnalyzer.summary(target,rows,self.config.repeats,eligible=error is None)
                    summary.update(batch_duration_s=max(0,time.monotonic()-batch_start) if batch_start else 0,
                                   capture_batch_duration_s=captured[-1][3]-batch_start if captured else 0,
                                   on_duration_s=self._off_at-on_start if on_start and self._off_at else 0)
                    summary['capture_batch_target_met']=bool(captured and summary['capture_batch_duration_s']<=1.5 and len(captured)==self.config.repeats)
                    self._csv(point_folder / 'runs.csv', rows)
                    (point_folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
                    self.summaries.append(summary);self.result['revision']+=1;self.persist()
                except BaseException as exc:error=error or exc
                if error:raise error
                self.publish('saved',point=index,target_a=target,repeat=len(captured))
                self._cool()
            self.result['status']='complete'
        except BaseException as exc:
            self.result['status']='stopped' if self.stop_event.is_set() or isinstance(exc,KeyboardInterrupt) else 'error'
            self.result['reason']=str(exc) or type(exc).__name__
            self.result['interrupted']=isinstance(exc,KeyboardInterrupt)
            self.event('aborted',reason=self.result['reason'])
        finally:
            self._point_done.set()
            try:self.off()
            except Exception as exc:self.result.update(status='error',reason=str(exc))
            if self._unknown_latched:self.result.update(status='error',output_state='unknown',reason='输出状态未知；需要人工检查，禁止继续')
            if self.folder:
                try:self.persist()
                except Exception as exc:self.result.update(status='error',reason='保存失败：'+str(exc))
            self.publish(self.result['status'])
        return self.result
