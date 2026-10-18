"""Regression: failed repetitions never contribute to any signal statistic."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import numpy as np
from pico4824a.bias_scan import BiasScanAnalyzer, BiasScanController, SimulatedPowerSupply
from pico4824a.bias_scan.capture_validity import assess_capture
from pico4824a.storage import load_npz
from test_bias_waveform_guard import setup, signal

ROOT=Path(__file__).resolve().parents[1]

class CaptureValidityTests(unittest.TestCase):
    def test_noise_only_is_invalid_but_weak_pzt_is_valid(self):
        acq,cfg=setup()
        good=signal(acq)
        good.volts['A']*=1e-10
        self.assertTrue(assess_capture(good,cfg)['valid'])
        # One weak monitor does not disqualify an otherwise observable burst.
        good.volts['C']*=0
        self.assertTrue(assess_capture(good,cfg)['valid'])
        bad=signal(acq)
        rng=np.random.default_rng(4824)
        for ch in bad.volts:
            bad.volts[ch]=.05+rng.normal(0,1e-4,len(bad.time_s))
        q=assess_capture(bad,cfg)
        self.assertFalse(q['valid'])
        self.assertEqual(q['status'],'EXCITATION_NOT_CAPTURED')

    def test_late_ready_is_discarded_even_when_waveform_contains_signal(self):
        acq,cfg=setup()
        acq.trigger.enabled=True;acq.trigger.source='B';acq.trigger.threshold_v=.1
        acq.trigger.auto_trigger_ms=100;acq.trigger.direction='rising'
        good=signal(acq);good.capture_info['ready_wait_s']=.2
        self.assertEqual(assess_capture(good,cfg)['status'],'TRIGGER_TIMEOUT')
        good.capture_info['ready_wait_s']=.05
        self.assertTrue(assess_capture(good,cfg)['valid'])

    def test_no_pzt_amplitude_gate_without_excitation_monitors(self):
        acq,cfg=setup();cfg=replace(cfg,excitation_voltage_channel='',excitation_current_channel='')
        good=signal(acq)
        for ch in good.volts:good.volts[ch]*=0
        self.assertTrue(assess_capture(good,cfg)['valid'])
        good.capture_info['auto_triggered']=True
        self.assertFalse(assess_capture(good,cfg)['valid'])

    def test_all_signal_statistics_filter_invalid_rows_first(self):
        rows=[dict(valid=v,capture_valid=v,actual_current_a=x,vpp_v=x,
                   excitation_voltage_vpp_v=2*x,excitation_current_vpp_a=3*x)
              for v,x in [(True,1),(True,2),(False,0),(True,3),(True,4)]]
        for method,expected,n in [('mean',2.5,4),('median',2.5,4),('trimmed_mean',2.5,2)]:
            s=BiasScanAnalyzer.summary(.5,rows,5,method=method)
            self.assertEqual(s['vpp_mean_v'],expected)
            self.assertEqual(s['excitation_voltage_mean_v'],expected*2)
            self.assertEqual(s['excitation_current_mean_a'],expected*3)
            self.assertEqual(s['statistics_count'],n)
            self.assertEqual(s['excitation_current_statistics_count'],n)
            self.assertAlmostEqual(s['vpp_sd_v'],np.std([2,3] if method=='trimmed_mean' else [1,2,3,4],ddof=1))
            self.assertEqual(s['failed_capture_count'],1)
            self.assertFalse(s['eligible'])
        for r in rows:r['valid']=False
        s=BiasScanAnalyzer.summary(.5,rows,5)
        for key in ['vpp_mean_v','vpp_sd_v','excitation_voltage_mean_v','excitation_current_mean_a']:
            self.assertIsNone(s[key])

    def test_failed_frames_retried_until_five_valid_without_new_power_cycle(self):
        for failures in [{3},{2,4},set(range(1,6))]:
            with self.subTest(failures=failures),tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
                acq,cfg=setup();cfg=replace(cfg,waveform_guard_enabled=False,aggregation='mean')
                class Adapter:
                    count=0
                    def prepare(self):pass
                    def stop(self):pass
                    def capture(self,config,remaining):
                        self.count+=1;r=signal(config)
                        if self.count in failures:
                            for ch in r.volts:r.volts[ch]=np.full(len(r.time_s),.05)
                        return r
                adapter=Adapter();power=SimulatedPowerSupply()
                job=BiasScanController(cfg,acq,power,adapter,folder,simulate=True)
                result=job.run()
                self.assertEqual(result['status'],'complete',result['reason'])
                self.assertEqual(adapter.count,5+len(failures))
                self.assertEqual(sum(e['event']=='on' for e in result['events']),1)
                s=result['summary'][0]
                self.assertEqual(s['valid_count'],5)
                self.assertEqual(s['invalid_count'],len(failures))
                self.assertEqual(result['best_current_a'],.5)
                valid=[r for r in result['runs'] if r['valid']]
                for metric,mean,sd in [('vpp_v','vpp_mean_v','vpp_sd_v'),('excitation_voltage_vpp_v','excitation_voltage_mean_v','excitation_voltage_sd_v'),('excitation_current_vpp_a','excitation_current_mean_a','excitation_current_sd_a')]:
                    expected=[r[metric] for r in valid]
                    if expected:
                        self.assertAlmostEqual(s[mean],np.mean(expected))
                        self.assertAlmostEqual(s[sd],np.std(expected,ddof=1))
                    else:self.assertIsNone(s[mean]);self.assertIsNone(s[sd])
                self.assertEqual(len(list(job.folder.rglob('*.npz'))),5+len(failures))
                for row in result['runs']:
                    loaded=load_npz(job.folder/row['file'])
                    self.assertEqual(loaded.capture_info['bias_validation']['valid'],row['valid'])
                    if not row['valid']:
                        self.assertTrue(row['invalid_reason'])
                        np.testing.assert_array_equal(loaded.volts['B'],np.full(len(loaded.time_s),.05))
                powered=False
                for e in result['events']:
                    if e['event']=='on':powered=True
                    if e['event']=='off_confirmed':powered=False
                    if e['event'] in {'analyze','save_waveform','save_task'}:self.assertFalse(powered)

    def test_legacy_npz_without_capture_metadata_loads(self):
        acq,cfg=setup();r=signal(acq)
        metadata=dict(sample_interval_s=r.sample_interval_s,requested_sample_rate_hz=r.requested_sample_rate_hz,
                      actual_sample_rate_hz=r.actual_sample_rate_hz,config=acq.to_dict())
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
            path=Path(folder)/'legacy.npz'
            np.savez(path,metadata_json=json.dumps(metadata),time_s=r.time_s,ch_A_v=r.volts['A'])
            loaded=load_npz(path)
            self.assertEqual(loaded.capture_info,{})
            np.testing.assert_array_equal(loaded.time_s,r.time_s)
            np.testing.assert_array_equal(loaded.volts['A'],r.volts['A'])

    def test_permanent_failure_stops_at_original_deadline(self):
        import time
        acq,cfg=setup();cfg=replace(cfg,waveform_guard_enabled=False,max_on_s=.12,stable_timeout_s=.1)
        class Adapter:
            def prepare(self):pass
            def stop(self):pass
            def capture(self,config,remaining):
                time.sleep(.01)
                r=signal(config)
                for ch in r.volts:r.volts[ch]*=0
                return r
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
            power=SimulatedPowerSupply()
            result=BiasScanController(cfg,acq,power,Adapter(),folder,simulate=True).run()
            self.assertEqual(result['status'],'error')
            self.assertEqual(result['output_state'],'off')
            self.assertEqual(result['summary'][0]['valid_count'],0)
            self.assertIsNone(result['best_current_a'])
            self.assertEqual(sum(e['event']=='on' for e in result['events']),1)
            self.assertLess(result['summary'][0]['on_duration_s'],.5)

    def test_sdk_timeout_and_late_ready_both_retry_same_valid_slot(self):
        from pico4824a.device import CaptureTimeout
        from pico4824a.web import WebControlState
        acq,cfg=setup();cfg=replace(cfg,waveform_guard_enabled=False,aggregation='mean')
        acq.trigger.enabled=True;acq.trigger.auto_trigger_ms=100
        class Adapter:
            count=0
            def prepare(self):pass
            def stop(self):pass
            def capture(self,config,remaining):
                self.count+=1
                if self.count==1:raise CaptureTimeout('capture timed out')
                r=signal(config)
                r.capture_info['ready_wait_s']=.2 if self.count==2 else .005
                return r
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
            adapter=Adapter();job=BiasScanController(cfg,acq,SimulatedPowerSupply(),adapter,folder,simulate=True)
            result=job.run()
            self.assertEqual(result['status'],'complete',result['reason'])
            self.assertEqual(adapter.count,7)
            self.assertEqual(result['summary'][0]['capture_attempt_count'],7)
            self.assertEqual(result['summary'][0]['capture_timeout_count'],1)
            self.assertEqual(result['summary'][0]['valid_count'],5)
            self.assertEqual(result['runs'][0]['capture_status'],'TRIGGER_TIMEOUT')
            self.assertEqual(result['runs'][0]['attempt'],2)
            self.assertEqual(result['runs'][1]['attempt'],3)
            self.assertEqual(sum(e['event']=='capture_timeout' for e in result['events']),1)
            state=WebControlState();state.bias_controller=job
            self.assertFalse(state.bias_preview(0,1,2)['row']['valid'])
            self.assertTrue(state.bias_preview(0,1,3)['row']['valid'])
