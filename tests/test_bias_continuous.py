"""One fixed bias, all amplitudes: shared deadline and OFF before waveform I/O."""
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from test_bias_waveform_guard import setup, Adapter
from pico4824a.bias_scan import BiasScanController, SimulatedPowerSupply

ROOT = Path(__file__).resolve().parents[1]


class ContinuousBiasTests(unittest.TestCase):
    def config(self, **values):
        acquisition, config = setup()
        config = replace(config, waveform_guard_enabled=False, amplitude_mode='sweep',
                         amplitude_start_vpp=.5, amplitude_stop_vpp=1, amplitude_step_vpp=.5)
        return acquisition, replace(config, **values)

    def run_job(self, config, acquisition, factory=Adapter):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp')
        self.addCleanup(self.tmp.cleanup)
        power = SimulatedPowerSupply()
        job = BiasScanController(config, acquisition, power, factory(power), self.tmp.name, simulate=True)
        return job, job.run()

    def test_two_amplitudes_one_on_and_one_cooling(self):
        acquisition, config = self.config()
        cooling = []
        with patch.object(BiasScanController, '_cool', lambda owner, delay:cooling.append(delay)):
            job, result = self.run_job(config, acquisition)
        self.assertEqual(result['status'], 'complete', result['reason'])
        self.assertEqual(len(result['runs']),10)
        self.assertEqual([r['awg_vpp'] for r in result['summary']],[.5,1])
        self.assertEqual(sum(e['event']=='on' for e in result['events']),1)
        self.assertEqual(len(cooling),1)
        events=[e['event'] for e in result['events'] if e['event']!='telemetry']
        first=events.index('on');last=events.index('off_requested',first)
        self.assertEqual(events[first:last].count('capture_start'),10)
        self.assertNotIn('save_waveform',events[first:last])
        self.assertEqual(job._cooled_count,1)

    def test_bias_changes_start_a_new_cycle_preserving_scan_order(self):
        acquisition, config = self.config(stop_a=1)
        job, result = self.run_job(config, acquisition)
        self.assertEqual(result['status'],'complete',result['reason'])
        self.assertEqual([e['target_a'] for e in result['events'] if e['event']=='on'],[.5,1])
        self.assertEqual(len(job.groups),2)
        alternate=replace(config, amplitude_order='amplitude_outer').scan_groups(acquisition)
        self.assertEqual([g[0][1] for g in alternate],[.5,1,.5,1])

    def test_amplitude_change_does_not_reset_deadline_and_partial_data_is_saved(self):
        class Slow(Adapter):
            def capture(self, acq, remaining):
                time.sleep(.02)
                return super().capture(acq, remaining)
        acquisition, config=self.config(max_on_s=.2,stable_timeout_s=.05)
        job, result=self.run_job(config,acquisition,Slow)
        self.assertEqual(result['status'],'error')
        self.assertIn('最大通电',result['reason'])
        self.assertEqual(sum(e['event']=='on' for e in result['events']),1)
        self.assertEqual(result['output_state'],'off')
        self.assertGreater(len(result['runs']),0)
        self.assertLess(len(result['runs']),10)
        self.assertTrue(all(not s['eligible'] for s in result['summary'] if s['awg_vpp']==1))

    def test_guarded_retry_does_not_rearm_power(self):
        acquisition, config=self.config(waveform_guard_enabled=True,auto_range_enabled=True)
        job, result=self.run_job(config,acquisition,lambda p:Adapter(p,'retry'))
        self.assertEqual(result['status'],'complete',result['reason'])
        self.assertEqual(sum(e['event']=='on' for e in result['events']),1)
        retries=sum(e['event']=='range_retry' for e in result['events'])
        self.assertGreaterEqual(retries,1)
        self.assertEqual(len(result['runs']),10+retries)

    def test_group_memory_budget_includes_all_amplitudes(self):
        acquisition,config=self.config(memory_limit_mb=.7)
        with self.assertRaisesRegex(ValueError,'内存预算'):
            config.acquisition(acquisition)

    def test_zero_bias_never_enables_output(self):
        acquisition,config=self.config(start_a=0,stop_a=0)
        _,result=self.run_job(config,acquisition)
        self.assertEqual(result['status'],'complete',result['reason'])
        self.assertEqual(len(result['runs']),10)
        self.assertFalse(any(e['event']=='on' for e in result['events']))

    def test_unknown_output_defers_waveform_save_until_explicit_recovery(self):
        class Power(SimulatedPowerSupply):
            fail=True
            def output_off(self):
                if self.fail and self.output_state in {'on','unknown'}:
                    self.output_state='unknown'
                    raise TimeoutError('lost OFF confirmation')
                super().output_off()
        acquisition,config=self.config()
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as tmp:
            power=Power()
            job=BiasScanController(config,acquisition,power,Adapter(power),tmp,simulate=True)
            result=job.run()
            self.assertEqual(result['output_state'],'unknown')
            self.assertFalse(list(job.folder.rglob('*.npz')))
            self.assertEqual(len(job._pending_group[1]),10)
            power.fail=False
            power.output_off()
            job._unknown_latched=False
            job.save_pending()
            self.assertEqual(len(list(job.folder.rglob('*.npz'))),10)
            self.assertEqual(sum(e['event']=='on' for e in result['events']),1)
