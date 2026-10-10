"""Guarded bias/amplitude scanning: OFF precedes diagnosis, retry and waveform I/O."""
import json
import math
import threading
import time
from ..config import AcquisitionConfig
from ..storage import save_npz
from .analyzer import BiasScanAnalyzer
from .quality import assess, suggest_ranges


def finish_cycle(owner):
    owner._point_done.set()
    try:owner.off()
    finally:
        for thread in owner._threads:thread.join(timeout=.8)
        if any(thread.is_alive() for thread in owner._threads):
            owner.result['instrument_cleanup_pending']=True
            raise RuntimeError('安全监控未退出，禁止继续')
        if not owner._unknown_latched:owner.protection.disarm()


def capture_once(owner, acquisition, target, index, repeat, remaining_on):
    owner._point_done=threading.Event();owner._threads=[];owner._telemetry=None
    started=None;capture=None;telemetry=None
    try:
        owner.check()
        if target==0:
            owner.off();owner._telemetry=owner.power.read_off_actual()
            if not math.isfinite(owner._telemetry['current_a']) or abs(owner._telemetry['current_a'])>owner.config.stable_abs_a:
                raise RuntimeError('断电基线电流异常')
        else:
            if remaining_on<=owner.config.stable_hold_s:raise RuntimeError('本组合累计通电时间已用尽')
            owner.power.configure(owner.limits['voltage_limit_v'],target)
            owner.protection.arm(remaining_on);owner.check()
            owner._on_at=time.monotonic()
            owner._threads=[threading.Thread(target=owner._deadline,args=(owner._on_at+remaining_on,owner._point_done),daemon=True)]
            owner._threads[0].start();owner.power.output_on();owner.event('on',target_a=target)
            monitor=threading.Thread(target=owner._monitor,args=(owner._point_done,),daemon=True);owner._threads.append(monitor);monitor.start()
            owner.publish('stabilizing',point=index,target_a=target,repeat=repeat,awg_vpp=acquisition.awg.pk_to_pk_v)
            owner._stable(target)
        owner.publish('capturing',point=index,target_a=target,repeat=repeat,awg_vpp=acquisition.awg.pk_to_pk_v)
        started=time.monotonic();telemetry=dict(owner._telemetry)
        owner.event('capture_start',point=index,repeat=repeat)
        remaining=(owner._on_at+remaining_on-started) if owner._on_at else acquisition.capture_timeout_s
        if remaining<=0:raise RuntimeError("累计通电时间超限")
        capture=owner.adapter.capture(acquisition,remaining)
        ended=time.monotonic()
        # First operation after capture is OFF. No waveform diagnosis or I/O beforehand.
        finish_cycle(owner)
        owner.event('capture_return',point=index,repeat=repeat,returned_at=ended)
        owner.check()
        return capture,telemetry,started,ended
    except BaseException:
        if capture is not None:
            # Preserve the returned raw capture even when OFF confirmation failed.
            owner._pending_failed_capture=(capture,telemetry,started,time.monotonic())
        finish_cycle(owner)
        raise


def run_guarded(owner):
    config=owner.config;owner.result['scan_plan']=[{'target_a':i,'awg_vpp':v} for i,v in owner.plan]
    owner.result['guarded']=True
    current_ranges={name:channel.range for name,channel in owner.acquisition.channels.items()}
    context={'point':0,'target_a':None,'awg_vpp':None,'repeat':0};last_started=None
    try:
        owner._prepare_run()
        for index,(target,amplitude) in enumerate(owner.plan):
            context.update(point=index,target_a=target,awg_vpp=amplitude,repeat=0)
            owner.check();owner._point_peak_a=target;reference=None;rows=[];attempt_rows=[];on_total=0.;group_at=time.monotonic();last_returned=None
            point_folder=owner.folder/f'电流{target:.3f}A'/f'{index:03d}_AWG{amplitude:.3f}Vpp';point_folder.mkdir(parents=True,exist_ok=True)
            owner.publish('setting',**context)
            acquisition=AcquisitionConfig.from_dict(owner.acquisition.to_dict());acquisition.awg.pk_to_pk_v=amplitude
            for channel,name in current_ranges.items():acquisition.channels[channel].range=name
            acquisition.validate()
            for repeat in range(1,config.repeats+1):
                context['repeat']=repeat
                for attempt in range(1,config.range_max_retries+2):
                    owner.check()
                    if last_started is not None:owner.stop_event.wait(max(0,last_started+config.interval_s-time.monotonic()))
                    owner.check();owner._last_on_duration=0
                    capture,telemetry,started,ended=capture_once(owner,acquisition,target,index,repeat,owner.limits['max_on_s']-on_total)
                    last_started=started;last_returned=ended;on_total+=owner._last_on_duration
                    owner._pending_failed_capture=(capture,telemetry,started,ended)
                    owner.event('quality_check',point=index,repeat=repeat,attempt=attempt)
                    diagnosis=assess(capture,acquisition,config,reference)
                    changes,range_error=suggest_ranges(capture,acquisition,config) if config.auto_range_enabled else ({},None)
                    if range_error and diagnosis['status']=='OK':diagnosis.update(status='ADC_RANGE',reason=range_error)
                    retry=diagnosis['status']=='ADC_RANGE' and bool(changes) and not range_error and attempt<=config.range_max_retries
                    if diagnosis['status']=='ADC_RANGE' and not retry:
                        diagnosis['reason']+=(('；'+range_error) if range_error else '；自动量程关闭或重采次数用尽')
                    value,reason=BiasScanAnalyzer.vpp(capture,config)
                    if value is None and diagnosis['status']=='OK':diagnosis.update(status='INVALID',reason=reason or 'PZT 无法评价')
                    valid=diagnosis['status']=='OK' and value is not None
                    row={'point':index,'repeat':repeat,'attempt':attempt,'target_a':target,'awg_vpp':amplitude,
                         'actual_current_a':telemetry['current_a'],'capture_start':started,'capture_end':ended,'capture_duration_s':ended-started,
                         'telemetry_monotonic_time':telemetry['monotonic_time'],'telemetry_wall_time':telemetry.get('wall_time'),
                         'vpp_v':value,'vpp_basis':'filtered' if config.filter_enabled else 'raw','valid':valid,
                         'invalid_reason':diagnosis['reason'] or reason,'quality_status':diagnosis['status'],
                         'range_retry':retry,'current_range':capture.config.channels[config.excitation_current_channel].range,
                         'voltage_range':capture.config.channels[config.excitation_voltage_channel].range,
                         'pzt_range':capture.config.channels[config.pzt_channel].range,
                         'on_duration_s':owner._last_on_duration,'file':(point_folder/f'重复{repeat:02d}_尝试{attempt:02d}__原始波形.npz').relative_to(owner.folder).as_posix(),
                         **{k:v for k,v in diagnosis.items() if k not in {'status','reason'}}}
                    row.update(BiasScanAnalyzer.excitation(capture,config))
                    owner.event('save_waveform',point=index,repeat=repeat,attempt=attempt)
                    save_npz(capture,owner.folder/row['file']);owner._pending_failed_capture=None;attempt_rows.append(row);owner.rows.append(row)
                    owner._csv(point_folder/'runs.csv',attempt_rows);owner.persist()
                    if not valid and not retry:
                        owner.result['safety_alarm']={**context,**diagnosis,'raw_file':row['file'],'output_state':owner.power.output_state}
                        raise RuntimeError(diagnosis['reason'] or reason or '波形无法可靠评价')
                    if retry:
                        owner.event('range_retry',point=index,repeat=repeat,changes=changes)
                        for channel,name in changes.items():acquisition.channels[channel].range=name
                    else:
                        rows.append(row);owner._captured_count+=1
                        if reference is None:reference=diagnosis
                        # Downrange only after a healthy diagnosis; changes never increase amplifier drive.
                        for channel,name in changes.items():acquisition.channels[channel].range=name
                        current_ranges={name:channel.range for name,channel in acquisition.channels.items()}
                    cooldown=config.cooldown_for(max(target,owner._point_peak_a) if target else 0,owner.simulate)
                    owner.publish('diagnosed',**context,quality_status=diagnosis['status'])
                    owner._cool(cooldown)
                    if not retry:break
            summary=BiasScanAnalyzer.summary(target,rows,config.repeats,method=config.aggregation)
            h2=[r['receiver_h2_h1_pct'] for r in rows if r.get('receiver_h2_h1_pct') is not None]
            summary.update(point=index,awg_vpp=amplitude,receiver_h2_h1_pct=BiasScanAnalyzer.aggregate(h2,config.aggregation)[0],
                on_duration_s=on_total,batch_duration_s=time.monotonic()-group_at,
                capture_batch_duration_s=(last_returned-group_at) if last_returned else 0,
                capture_batch_target_met=False,cooldown_s=config.cooldown_for(max(target,owner._point_peak_a) if target else 0,owner.simulate),cooling_mode=config.cooling_mode)
            owner.summaries.append(summary);owner._cooled_count+=1;owner.result['revision']+=1
            (point_folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            owner.persist();owner.publish('saved',**context)
        owner.result['status']='complete'
    except BaseException as exc:
        # DC OFF takes precedence even over waveform serialization or the warning UI.
        try:finish_cycle(owner)
        except Exception as off_error:exc=off_error
        try:
            owner.adapter.stop()
            if hasattr(owner.adapter,'disable_excitation'):owner.adapter.disable_excitation()
        except Exception as stop_error:owner.event('pico_stop_failed',reason=str(stop_error))
        owner.result['status']='stopped' if owner.stop_event.is_set() or isinstance(exc,KeyboardInterrupt) else 'error'
        owner.result['reason']=str(exc) or type(exc).__name__
        owner.result.setdefault('safety_alarm',{**context,'status':'ERROR','reason':owner.result['reason'],'output_state':owner.power.output_state})
        owner.result['safety_alarm']['output_state']=owner.power.output_state
        owner.event('aborted',reason=owner.result['reason'])
        failed=getattr(owner,'_pending_failed_capture',None)
        if failed and owner.folder:
            try:
                path=owner.folder/'异常返回__原始波形.npz';save_npz(failed[0],path)
                owner.result['safety_alarm']['raw_file']=path.name
            except Exception as save_error:owner.event('failed_capture_save_error',reason=str(save_error))
    finally:owner._finalize_run()
    return owner.result
