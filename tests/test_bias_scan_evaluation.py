"""Software-only mode and filtered evaluation; no physical instruments used."""
import tempfile
import unittest
from dataclasses import replace, asdict
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from pico4824a.analysis import fft_bandpass
from pico4824a.bias_scan import BiasScanConfig, BiasScanAnalyzer, BiasScanController, SimulatedPowerSupply
from pico4824a.bias_scan.safety import SoftwareProtection
from pico4824a.config import AcquisitionConfig
from pico4824a.web import WebControlState
from test_bias_scan import FakePico, FaultPower

ROOT = Path(__file__).resolve().parents[1]

class BiasEvaluationTests(unittest.TestCase):
    def config(self, **kw):
        return replace(BiasScanConfig(start_a=.5, stop_a=.5, repeats=5, interval_s=0,
            stable_hold_s=.01, poll_s=.01, voltage_limit_v=40, max_on_s=.5,
            stable_timeout_s=.15, cooldown_s=0, protection_mode='software'), **kw)

    def test_software_mode_needs_no_external_adapter_but_requires_limits(self):
        self.config().validate(live=True)
        with self.assertRaisesRegex(ValueError, '限值待定'):
            replace(self.config(), max_on_s=None).validate(live=True)
        with self.assertRaises(ValueError):
            self.config(stop_a=6.5).validate(live=True)
        with self.assertRaisesRegex(ValueError, '保护'):
            self.config(protection_mode='independent').validate(live=True)
        with self.assertRaises(ValueError):
            SoftwareProtection().arm(float('nan'))

    def test_web_software_preflight_without_adapter(self):
        state=WebControlState()
        state.bias_power=SimulatedPowerSupply()
        try:
            p=state.bias_preflight({'simulate':False, 'bias':asdict(self.config()),
                'config':AcquisitionConfig(sample_rate_hz=500000).to_dict()})
            self.assertEqual(p['protection_mode'], 'software')
            self.assertTrue(p['warnings'])
        finally:
            state.bias_power=None
            state.shutdown()

    def test_whole_record_filtered_before_window_raw_unchanged(self):
        fs=2e6
        t=np.arange(4000)/fs
        raw=.1*np.sin(2*np.pi*75000*t)+np.sin(2*np.pi*300000*t)
        original=raw.copy()
        result=SimpleNamespace(time_s=t, volts={'A':raw}, actual_sample_rate_hz=fs, overflow_channels=[])
        cfg=self.config()
        mask=(t*1e6>=cfg.direct_start_us)&(t*1e6<=cfg.direct_end_us)
        expected=fft_bandpass(raw,fs,cfg.filter_low_hz,cfg.filter_high_hz,cfg.filter_transition_hz)
        value, reason=BiasScanAnalyzer.vpp(result,cfg)
        self.assertEqual(reason,'')
        self.assertAlmostEqual(value,float(np.ptp(expected[mask])),places=14)
        raw_value,_=BiasScanAnalyzer.vpp(result,replace(cfg,filter_enabled=False))
        self.assertLess(value,raw_value/5)
        np.testing.assert_array_equal(raw,original)
        np.testing.assert_allclose(BiasScanAnalyzer.evaluation_signal(result,cfg),expected)
        raw[0]=np.nan
        self.assertIsNone(BiasScanAnalyzer.vpp(result,cfg)[0])

    def test_filter_validation_and_actual_nyquist(self):
        with self.assertRaises(ValueError):self.config(filter_low_hz=100000).validate()
        acq=AcquisitionConfig(sample_rate_hz=100000)
        with self.assertRaisesRegex(ValueError,'一半'):self.config().acquisition(acq)
        result=SimpleNamespace(time_s=np.arange(200)/100000,volts={'A':np.ones(200)},actual_sample_rate_hz=100000,overflow_channels=[])
        value,reason=BiasScanAnalyzer.vpp(result,self.config())
        self.assertIsNone(value)
        self.assertIn('滤波评价失败',reason)

    def test_live_codepath_with_fake_devices_and_software_deadline(self):
        # simulate=False exercises the real orchestration path; all devices are fakes.
        for mode in ['normal','blocked','pico']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
                power=SimulatedPowerSupply()
                job=BiasScanController(self.config(),AcquisitionConfig(sample_rate_hz=500000),
                    power,FakePico(mode),folder,simulate=False)
                self.assertIsInstance(job.protection,SoftwareProtection)
                r=job.run()
                self.assertEqual(r['output_state'],'off')
                self.assertEqual(r['status'],'complete' if mode=='normal' else 'error',r['reason'])
                self.assertEqual(r['evaluation']['basis'],'filtered')
                if mode=='normal':
                    self.assertTrue(all(row['vpp_basis']=='filtered' for row in r['runs']))
                if mode=='blocked':self.assertIn('通电',r['reason'])

    def test_software_mode_still_shuts_off_on_faults(self):
        for fault in ['communication','overcurrent','unstable','off']:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
                job=BiasScanController(self.config(),AcquisitionConfig(sample_rate_hz=500000),
                    FaultPower(fault),FakePico(),folder,simulate=False)
                r=job.run()
                self.assertEqual(r['status'],'error')
                self.assertEqual(r['output_state'],'unknown' if fault=='off' else 'off')

    def test_current_cooling_schedule_and_validation(self):
        cfg=self.config(cooling_mode='current',cooldown_s=36,cooldown_min_s=1)
        cfg.validate(live=True)
        self.assertEqual([cfg.cooldown_for(a) for a in [0,.5,1,3,6]], [0,1,1,9,36])
        self.assertGreater(cfg.cooldown_for(3.1),cfg.cooldown_for(3))
        with self.assertRaisesRegex(ValueError,'最短冷却'):
            replace(cfg,cooldown_min_s=None).validate(live=True)
        with self.assertRaises(ValueError):replace(cfg,cooldown_min_s=40).validate()
        with self.assertRaises(ValueError):cfg.cooldown_for(float('nan'))

    def test_current_cooling_saved_per_point(self):
        import json
        cfg=self.config(start_a=0,stop_a=1,cooling_mode='current',cooldown_s=.36,cooldown_min_s=0)
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
            job=BiasScanController(cfg,AcquisitionConfig(sample_rate_hz=500000),
                SimulatedPowerSupply(),FakePico(),folder,simulate=True)
            r=job.run()
            self.assertEqual(r['status'],'complete',r['reason'])
            self.assertEqual(r['summary'][0]['cooldown_s'],0)
            for row in r['summary']:
                self.assertAlmostEqual(row['cooldown_s'],.36*(row['target_a']/6)**2)
                self.assertEqual(row['cooling_mode'],'current')
                self.assertEqual(row['cooldown_current_a'],row['target_a'])
            saved=json.loads((job.folder/'result.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['summary'],r['summary'])

if __name__=='__main__':unittest.main()
