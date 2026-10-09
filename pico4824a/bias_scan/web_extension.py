"""HTTP-facing orchestration, separate from existing acquisition algorithms."""
import threading
import time
from pathlib import Path
from dataclasses import asdict, replace
import numpy as np
from ..config import AcquisitionConfig
from ..storage import load_npz
from ..storage_naming import preferences
from . import BiasScanConfig, BiasScanController, PicoCaptureAdapter, IT6524DController, SimulatedPowerSupply
from .safety import SafetyProtection, TemperatureProvider
from .analyzer import BiasScanAnalyzer


class BiasScanWebMixin:
    def init_bias_scan(self, project):
        self.bias_output_root=Path(project)/'data/bias_scans'
        self.bias_controller=None;self.bias_result=None;self.bias_power=None
        self.bias_protection=SafetyProtection();self.bias_temperature=TemperatureProvider()
        self.bias_unknown=False;self.bias_cleanup_pending=False

    def bias_resources(self):
        with self._lock:
            if self.status.state in {'running','paused'} or self.bias_cleanup_pending:
                raise RuntimeError('已有仪器任务正在运行，不能刷新电源资源')
            manager = getattr(self.bias_power, 'manager', None)
            return IT6524DController.resources(manager=manager)

    def bias_connect(self, raw):
        with self._lock:
            if self.status.state in {'running','paused'} or self.bias_cleanup_pending:raise RuntimeError('已有仪器任务正在运行')
            if self.bias_power:
                try:self.bias_power.close()
                except Exception as exc:
                    self.bias_unknown=True
                    raise RuntimeError('原连接未确认关闭，请点击确认断电并恢复：'+str(exc)) from exc
                self.bias_power=None
            power=IT6524DController(str(raw.get('resource','')),str(raw.get('backend','')))
            self.bias_power=power
            try:identity=power.connect()
            except BaseException:
                self.bias_unknown=power.output_state!='off'
                raise
            self.bias_unknown=False
            return {'identity':identity,'output_state':power.output_state,'independent_cutoff_ready':self.bias_protection.ready()}

    def bias_recover(self):
        """Explicit OFF-only recovery. Never resume an interrupted scan."""
        with self._lock:
            controller=self.bias_controller
            if self.status.state in {'running','paused'} or (self._worker_thread and self._worker_thread.is_alive()):
                raise RuntimeError('请先停止扫描并等待采集退出，再确认断电并恢复')
            if controller and any(t.is_alive() for t in controller._threads):
                raise RuntimeError('安全监控尚未退出，不能解除仪器占用')
            powers=[self.bias_power] if self.bias_power else []
            if controller and controller.power not in powers:powers.append(controller.power)
            if not powers:raise RuntimeError('尚无电源连接，请先选择 USB 资源并连接')
            try:
                for power in powers:
                    try:
                        power.connect()
                        power.output_off()
                    except Exception:
                        if not isinstance(power,IT6524DController):raise
                        # Reopen only the same user-selected USB resource. Model
                        # identity and OFF are confirmed again before unlocking.
                        power.close_transport()
                        power.connect()
                        power.output_off()
                    if power.output_state!='off':raise RuntimeError('电源未确认关闭')
            except Exception as exc:
                self.bias_unknown=True
                self.status.state='error';self.status.task_kind='bias_scan'
                self.status.message='断电恢复失败：'+str(exc)
                self.status.progress={**(self.status.progress or {}),'output_state':'unknown','phase':'error'}
                raise RuntimeError(self.status.message) from exc
            self.bias_unknown=False;self.bias_cleanup_pending=False
            warning=''
            if controller:
                controller._unknown_latched=False
                controller.event('manual_off_recovery',output_state='off')
                controller.result['recovery']={'output_state':'off','wall_time':time.time()}
                if controller.folder:
                    try:controller.persist()
                    except Exception as exc:warning='；恢复记录保存失败：'+str(exc)
            self.status.state='idle';self.status.task_kind='bias_scan'
            self.status.message='已确认输出关闭，可以重新开始'+warning
            self.status.progress={**(self.status.progress or {}),'output_state':'off','phase':'recovered'}
            self.status.finished_at=time.time()
            return {'output_state':'off','message':self.status.message,'recovered':True}

    def bias_preflight(self, raw):
        simulate=raw.get('simulate',True)
        if not isinstance(simulate,bool):raise ValueError('simulate 必须为布尔值')
        config=BiasScanConfig.from_dict(raw.get('bias',{}))
        config.validate(live=not simulate)
        acquisition=config.acquisition(AcquisitionConfig.from_dict(raw.get('config',{})))
        if not simulate:
            if config.protection_mode == 'independent' and not self.bias_protection.ready():raise RuntimeError('未接入独立超时断电保护适配器；可改选仅软件保护模式')
            if not self.bias_power or self.bias_power.output_state!='off':raise RuntimeError('请连接并确认 IT6524D 输出关闭')
        return {'points':config.points(),'acquisition':acquisition.to_dict(),'limits':config.effective_limits(simulate),'simulated':simulate,
                'protection_mode':config.protection_mode,'warnings':['仅软件保护：程序强杀、USB断开或电脑断电时，无法保证自动断电。输出关闭不等于线圈储能已释放。'] if config.protection_mode == 'software' else []}

    def start_bias_scan(self, raw):
        with self._lock:
            if self.status.state in {'running','paused'} or self.bias_unknown or self.bias_cleanup_pending:raise RuntimeError('仪器忙或偏置输出状态未知')
            self.bias_preflight(raw)
            simulate=raw.get('simulate',True)
            config=BiasScanConfig.from_dict(raw.get('bias',{}))
            config=replace(config,scan_name=config.scan_name or preferences()['bias'])
            source=AcquisitionConfig.from_dict(raw.get('config',{}))
            self._stop_event.clear();self._resume_event.set();self._trip_alarm=None
            self.status.capture_id+=1;task_id=self.status.capture_id
            self.status.state='running';self.status.task_kind='bias_scan'
            self.status.started_at=time.time();self.status.finished_at=None
            self.status.message='偏置扫描预检 · SIMULATED' if simulate else '偏置扫描预检'
            self.status.progress=None;self.bias_result=None;self.bias_controller=None
            def work():
                device=None;temporary=False;power=None
                try:
                    device,temporary=self._device_for_task(simulate)
                    with self._lock:self.device=device
                    power=SimulatedPowerSupply() if simulate else self.bias_power
                    def progress(state):
                        with self._lock:
                            self.status.progress=state
                            labels={'setting':'设置电流','stabilizing':'等待稳定','capturing':'连续采集',
                                    'saved':'已断电 · 已保存','cooling':'断电冷却','complete':'扫描完成',
                                    'stopped':'扫描停止','error':'扫描中止'}
                            self.status.message='偏置扫描 · '+labels.get(state['phase'],state['phase'])+(' · SIMULATED' if simulate else '')
                    controller=BiasScanController(config,source,power,PicoCaptureAdapter(device),self.bias_output_root,
                        simulate=simulate,stop_event=self._stop_event,progress=progress,
                        protection=None if simulate else self.bias_protection,temperature=self.bias_temperature)
                    with self._lock:self.bias_controller=controller
                    result=controller.run()
                    with self._lock:
                        self.bias_result=result
                        self.bias_unknown=result['output_state']!='off'
                        self.bias_cleanup_pending=result.get('instrument_cleanup_pending',False)
                        final_state='error' if self.bias_unknown else result['status']
                        final_message=result['reason'] or ('扫描完成 · SIMULATED' if simulate else '扫描完成 · '+('仅软件保护' if config.protection_mode=='software' else '独立保护'))
                except BaseException as exc:
                    if power:
                        try:power.output_off()
                        except Exception:self.bias_unknown=True
                    final_state='error';final_message=str(exc)
                finally:
                    # This worker remains the owner until any blocking capture returns.
                    if temporary and device:
                        try:device.close()
                        except Exception:pass
                    with self._lock:
                        self.device=None;self._worker_thread=None;self.status.finished_at=time.time()
                        self.status.state=final_state;self.status.message=final_message
            self._worker_thread=threading.Thread(target=work,daemon=True,name=f'bias-scan-{task_id}')
            self._worker_thread.start()
            return task_id

    def bias_result_payload(self):
        with self._lock:
            controller=self.bias_controller
            result=self.bias_result or (controller.result if controller else None)
            if result is None:raise RuntimeError('尚无偏置扫描结果')
            # Publish only summaries saved after OFF, never stream raw powered captures.
            return {k:list(result.get(k,[])) if k in {'summary','runs'} else result.get(k) for k in ['status','simulated','reason','output_state','summary','runs','revision',
                'best_current_a','best_vpp_mean_v','ties_a','output_dir','device_identity','acquisition','configuration','protection_mode','evaluation','off_error','recovery']}

    def bias_preview(self, point, repeat):
        with self._lock:
            controller=self.bias_controller
            if controller is None or controller.folder is None:raise RuntimeError('尚无偏置扫描结果')
            rows=list(controller.rows);folder=controller.folder
        row=next((r for r in rows if r['point']==point and r['repeat']==repeat),None)
        if row is None:raise ValueError('记录不存在或尚未保存')
        result=load_npz(folder/row['file']);step=max(1,int(np.ceil(len(result.time_s)/4000)))
        evaluated = BiasScanAnalyzer.evaluation_signal(result, controller.config)
        return {'time_us':(result.time_s[::step]*1e6).tolist(),
                'volts':evaluated[::step].tolist(),
                'raw_volts':result.volts[controller.config.pzt_channel][::step].tolist(),
                'vpp_basis':'filtered' if controller.config.filter_enabled else 'raw',
                'channel':controller.config.pzt_channel,'row':row,'simulated':result.simulated}

    def bias_export(self, name):
        if name not in {'summary.csv','runs.csv','result.json','config.json'}:raise ValueError('导出文件无效')
        with self._lock:
            controller=self.bias_controller
            if controller is None or controller.folder is None:raise RuntimeError('尚无偏置扫描结果')
            if self.status.state in {'running','paused'}:raise RuntimeError('请等待扫描结束后导出')
            return (controller.folder/name).read_bytes()

    def bias_archive(self):
        # Compression runs only after the instrument task releases its ownership.
        with self._lock:
            controller=self.bias_controller
            if controller is None or controller.folder is None:raise RuntimeError('尚无偏置扫描结果')
            if self.status.state in {'running','paused'}:raise RuntimeError('请等待扫描结束后导出')
            if self.bias_unknown or self.bias_cleanup_pending:raise RuntimeError('请先确认断电和仪器收尾完成')
            folder=controller.folder.resolve()
            # Starting another instrument job uses this same lock. It cannot
            # enter its powered phase while this archive is being written.
            from ..data_sessions import archive_session
            return archive_session(folder)

    def bias_shutdown(self):
        if self.bias_controller:self.bias_controller.stop()
        if self.bias_power:
            try:self.bias_power.close()
            except Exception as exc:
                self.bias_unknown=True
                raise RuntimeError('退出时偏置输出状态未知：'+str(exc)) from exc
