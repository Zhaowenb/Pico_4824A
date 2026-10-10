"""Continuous adjacent bias groups; one stability check and deadline per group."""
import json
import math
import threading
import time

from ..config import AcquisitionConfig
from .analyzer import BiasScanAnalyzer
from .quality import assess, suggest_ranges


def finish_cycle(owner):
    owner._point_done.set()
    try:
        owner.off()
    finally:
        for thread in owner._threads:
            thread.join(timeout=.8)
        if any(thread.is_alive() for thread in owner._threads):
            owner.result['instrument_cleanup_pending'] = True
            raise RuntimeError('安全监控未退出，禁止继续')
        if not owner._unknown_latched:
            owner.protection.disarm()


def begin_cycle(owner, target, index):
    owner.check()
    owner._fault = None
    owner._telemetry = None
    owner._point_done = threading.Event()
    owner._threads = []
    owner._point_peak_a = target
    owner._last_on_duration = 0
    if target == 0:
        owner.off()
        owner._telemetry = owner.power.read_off_actual()
        if not math.isfinite(owner._telemetry['current_a']) or abs(owner._telemetry['current_a']) > owner.config.stable_abs_a:
            raise RuntimeError('断电基线电流异常')
        return None
    owner.power.configure(owner.limits['voltage_limit_v'], target)
    owner.protection.arm(owner.limits['max_on_s'])
    owner.check()
    started = owner._on_at = time.monotonic()
    deadline = started + owner.limits['max_on_s']
    owner._threads = [threading.Thread(target=owner._deadline, args=(deadline, owner._point_done), daemon=True)]
    owner._threads[0].start()
    owner.power.output_on()
    owner.event('on', target_a=target)
    monitor = threading.Thread(target=owner._monitor, args=(owner._point_done,), daemon=True)
    owner._threads.append(monitor)
    monitor.start()
    owner.publish('stabilizing', point=index, target_a=target, repeat=0)
    owner._stable(target)
    return started


def save_group(owner, group, records, error):
    """No waveform processing or disk writes until output OFF is confirmed."""
    if owner._unknown_latched or owner.power.output_state != 'off':
        owner._pending_group = (group, records, error)
        raise RuntimeError('输出状态未知，原始采集保留在内存；确认断电并恢复后再保存')
    config = owner.config
    target = group[0][1]
    analysis_error = None
    cooldown = config.cooldown_for(max(target, owner._point_peak_a) if target else 0, owner.simulate)
    last_saved_point = records[-1]['point'] if records else group[0][0]
    for index, _, amplitude in group:
        point_records = [r for r in records if r['point'] == index]
        if not point_records and index != group[0][0]:
            continue
        owner.event('analyze', point=index)
        point_folder = owner.folder / (f'{index:02d}_电流{target:.3f}A' + ('_断电基线' if target == 0 else ''))
        if config.amplitude_mode != 'snapshot' or config.waveform_guard_enabled:
            point_folder = owner.folder / f'电流{target:.3f}A' / f'{index:03d}_AWG{amplitude:.3f}Vpp'
        point_folder.mkdir(parents=True, exist_ok=True)
        attempts = []
        rows = []
        for record in point_records:
            capture, telemetry = record['capture'], record['telemetry']
            value, reason = BiasScanAnalyzer.vpp(capture, config)
            diagnosis = record.get('diagnosis')
            if diagnosis is not None and diagnosis['status'] == 'OK' and value is None:
                diagnosis.update(status='INVALID', reason=reason or 'PZT 无法评价')
                analysis_error = diagnosis['reason']
                owner.result['safety_alarm'] = {'point':index, 'target_a':target, 'awg_vpp':amplitude,
                                              'repeat':record['repeat'], **diagnosis, 'output_state':'off'}
            valid = value is not None and not record.get('interrupted') and (diagnosis is None or diagnosis['status'] == 'OK')
            repeat, attempt = record['repeat'], record['attempt']
            filename = (f'重复{repeat:02d}_尝试{attempt:02d}__原始波形.npz' if diagnosis is not None
                        else f'重复{repeat:02d}__PZT-{config.pzt_channel}__原始波形.npz')
            row = {'point':index, 'repeat':repeat, 'target_a':target, 'awg_vpp':amplitude,
                   'actual_current_a':telemetry['current_a'], 'telemetry_monotonic_time':telemetry['monotonic_time'],
                   'telemetry_wall_time':telemetry.get('wall_time'), 'capture_start':record['started'],
                   'capture_end':record['ended'], 'capture_duration_s':record['ended']-record['started'],
                   'vpp_v':value, 'vpp_basis':'filtered' if config.filter_enabled else 'raw',
                   'filter_low_hz':config.filter_low_hz, 'filter_high_hz':config.filter_high_hz,
                   'filter_transition_hz':config.filter_transition_hz, 'valid':valid,
                   'invalid_reason':record.get('interrupted_reason', '') or (diagnosis['reason'] if diagnosis else '') or reason,
                   'file':(point_folder / filename).relative_to(owner.folder).as_posix()}
            if diagnosis is not None:
                row.update(attempt=attempt, quality_status=diagnosis['status'], range_retry=record.get('retry', False),
                           current_range=capture.config.channels[config.excitation_current_channel].range,
                           voltage_range=capture.config.channels[config.excitation_voltage_channel].range,
                           pzt_range=capture.config.channels[config.pzt_channel].range,
                           **{k:v for k,v in diagnosis.items() if k not in {'status','reason'}})
            row.update(BiasScanAnalyzer.excitation(capture, config))
            owner.event('save_waveform', point=index, repeat=repeat, attempt=attempt)
            owner._save_capture(capture, owner.folder / row['file'])
            attempts.append(row)
            owner.rows.append(row)
            if not record.get('retry'):
                rows.append(row)
            if owner.result.get('safety_alarm', {}).get('point') == index and not valid:
                owner.result['safety_alarm']['raw_file'] = row['file']
        complete = len(rows) == config.repeats and all(r['valid'] for r in rows)
        summary = BiasScanAnalyzer.summary(target, rows, config.repeats, eligible=complete, method=config.aggregation)
        h2 = [r['receiver_h2_h1_pct'] for r in rows if r.get('receiver_h2_h1_pct') is not None]
        summary.update(point=index, awg_vpp=amplitude, bias_group=owner._group_index,
                       receiver_h2_h1_pct=BiasScanAnalyzer.aggregate(h2, config.aggregation)[0],
                       batch_duration_s=(point_records[-1]['ended']-point_records[0]['started']) if point_records else 0,
                       capture_batch_duration_s=(point_records[-1]['ended']-point_records[0]['started']) if point_records else 0,
                       on_duration_s=owner._last_on_duration,
                       capture_batch_target_met=bool(complete and point_records[-1]['ended']-point_records[0]['started'] <= 1.5),
                       cooldown_current_a=max(target, owner._point_peak_a) if target else 0,
                       cooldown_s=cooldown if index == last_saved_point else 0,
                       cooling_mode=config.cooling_mode, bias_group_complete=error is None,
                       bias_group_on_duration_s=owner._last_on_duration)
        owner._csv(point_folder / 'runs.csv', attempts)
        (point_folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        owner.summaries.append(summary)
        owner.result['revision'] += 1
    owner.persist()
    if analysis_error:
        raise RuntimeError(analysis_error)
    return cooldown


def run_batches(owner):
    config = owner.config
    guarded = config.waveform_guard_enabled
    current_ranges = {name:channel.range for name, channel in owner.acquisition.channels.items()}
    context = {'point':0, 'target_a':None, 'awg_vpp':None, 'repeat':0}
    try:
        owner._prepare_run()
        owner.result.update(guarded=guarded, scan_plan=[{'target_a':i, 'awg_vpp':v} for i,v in owner.plan],
                            power_cycle_scope='adjacent_bias_group')
        for group_index, group in enumerate(owner.groups):
            owner._group_index = group_index
            target = group[0][1]
            records = []
            error = None
            started = None
            finished = False
            previous = None
            context.update(point=group[0][0], target_a=target, awg_vpp=group[0][2], repeat=0)
            owner.publish('setting', bias_group=group_index, **context)
            try:
                started = begin_cycle(owner, target, group[0][0])
                for index, _, amplitude in group:
                    context.update(point=index, target_a=target, awg_vpp=amplitude, repeat=0)
                    acquisition = AcquisitionConfig.from_dict(owner.acquisition.to_dict())
                    acquisition.awg.pk_to_pk_v = amplitude
                    for channel, value in current_ranges.items():
                        acquisition.channels[channel].range = value
                    acquisition.validate()
                    reference = None  # Deliberately changing amplitude is not a repeated-signal fault.
                    owner.event('amplitude_point', point=index, target_a=target, awg_vpp=amplitude)
                    for repeat in range(1, config.repeats+1):
                        context['repeat'] = repeat
                        for attempt in range(1, (config.range_max_retries+2) if guarded else 2):
                            owner.check()
                            if previous is not None:
                                while time.monotonic() < previous+config.interval_s:
                                    owner.check()
                                    owner.stop_event.wait(max(0, previous+config.interval_s-time.monotonic()))
                            owner.check()
                            remaining = started+owner.limits['max_on_s']-time.monotonic() if started is not None else acquisition.capture_timeout_s
                            if remaining <= 0:
                                raise RuntimeError('单档最大通电时间超限')
                            owner.publish('capturing', **context)
                            capture_started = time.monotonic()
                            telemetry = dict(owner._telemetry)
                            owner.event('capture_start', point=index, repeat=repeat, attempt=attempt)
                            previous = time.monotonic()
                            capture = owner.adapter.capture(AcquisitionConfig.from_dict(acquisition.to_dict()), remaining)
                            ended = time.monotonic()
                            last = index == group[-1][0] and repeat == config.repeats
                            record = {'point':index, 'repeat':repeat, 'attempt':attempt, 'capture':capture,
                                      'telemetry':telemetry, 'started':capture_started, 'ended':ended}
                            if owner._fault or owner.stop_event.is_set() or (started is not None and ended >= started+owner.limits['max_on_s']):
                                record.update(interrupted=True, interrupted_reason=owner._fault or ('用户停止' if owner.stop_event.is_set() else '单档最大通电时间超限'))
                            # Unguarded final capture: OFF is the first operation after return.
                            if last and not guarded:
                                try:
                                    finish_cycle(owner)
                                    finished = True
                                finally:
                                    records.append(record)
                            else:
                                records.append(record)
                            owner.event('capture_return', point=index, repeat=repeat, returned_at=ended)
                            owner._capture_durations.append(ended-capture_started)
                            owner.check()
                            retry = False
                            if guarded:
                                owner.event('quality_check', point=index, repeat=repeat, attempt=attempt)
                                diagnosis = assess(capture, acquisition, config, reference)
                                record['diagnosis'] = diagnosis
                                owner.check()
                                changes, range_error = suggest_ranges(capture, acquisition, config) if config.auto_range_enabled else ({}, None)
                                if range_error and diagnosis['status'] == 'OK':
                                    diagnosis.update(status='ADC_RANGE', reason=range_error)
                                retry = diagnosis['status']=='ADC_RANGE' and bool(changes) and not range_error and attempt<=config.range_max_retries
                                record['retry'] = retry
                                if diagnosis['status'] != 'OK' and not retry:
                                    owner.result['safety_alarm'] = {**context, **diagnosis}
                                    raise RuntimeError(diagnosis['reason'] or '波形无法可靠评价')
                                if retry:
                                    owner.event('range_retry', point=index, repeat=repeat, changes=changes)
                                elif reference is None:
                                    reference = diagnosis
                                for channel, value in changes.items():
                                    acquisition.channels[channel].range = value
                                current_ranges = {name:channel.range for name, channel in acquisition.channels.items()}
                            if not retry:
                                owner._captured_count += 1
                                if last and not finished:
                                    finish_cycle(owner)
                                    finished = True
                                break
                owner.check()
            except BaseException as exc:
                error = RuntimeError(owner._fault) if owner._fault else exc
            finally:
                if not finished:
                    try:
                        finish_cycle(owner)
                    except Exception as exc:
                        error = exc
                if error is not None or owner._fault:
                    try:
                        owner.adapter.stop()
                        if hasattr(owner.adapter, 'disable_excitation'):
                            owner.adapter.disable_excitation()
                    except Exception as exc:
                        owner.event('pico_stop_failed', reason=str(exc))
            try:
                cooldown = save_group(owner, group, records, error)
            except BaseException as exc:
                error = error or exc
            if error is not None:
                raise error
            owner.publish('saved', **context)
            owner._cool(cooldown)
            owner._cooled_count += 1
        owner.result['status'] = 'complete'
    except BaseException as exc:
        owner.result['status'] = 'stopped' if owner.stop_event.is_set() or isinstance(exc, KeyboardInterrupt) else 'error'
        owner.result['reason'] = str(exc) or type(exc).__name__
        owner.result['interrupted'] = isinstance(exc, KeyboardInterrupt)
        if guarded:
            owner.result.setdefault('safety_alarm', {**context, 'status':'ERROR', 'reason':owner.result['reason']})
            owner.result['safety_alarm']['output_state'] = owner.power.output_state
        owner.event('aborted', reason=owner.result['reason'])
    finally:
        owner._finalize_run()
    return owner.result
