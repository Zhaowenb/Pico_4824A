import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import numpy as np
from pico4824a.bias_scan import BiasScanConfig, BiasScanAnalyzer, BiasScanController, SimulatedPowerSupply
from pico4824a.config import AcquisitionConfig
from test_bias_scan import FakePico
ROOT=Path(__file__).resolve().parents[1]

class ExcitationStatisticsTests(unittest.TestCase):
    def test_aggregation_and_sample_sd(self):
        values=[1,2,3,4,100]
        self.assertEqual(BiasScanAnalyzer.aggregate(values,'mean')[0],22)
        self.assertEqual(BiasScanAnalyzer.aggregate(values,'trimmed_mean'),(3.0,1.0,3))
        self.assertEqual(BiasScanAnalyzer.aggregate(values,'median')[0],3)
        self.assertEqual(BiasScanAnalyzer.aggregate([2,2,2,2,2],'trimmed_mean'),(2.0,0.0,3))
        self.assertEqual(BiasScanAnalyzer.aggregate([1,2],'trimmed_mean'),(None,None,0))

    def test_excitation_window_and_units(self):
        cfg=BiasScanConfig(excitation_voltage_channel='B',excitation_current_channel='C',
            excitation_voltage_scale=10,excitation_current_scale=2,excitation_start_us=0,excitation_end_us=2)
        result=SimpleNamespace(time_s=np.arange(4)*1e-6,volts={'B':np.array([0.,2.,-1.,100.]),'C':np.array([1,3,-2,100])},overflow_channels=[])
        metrics=BiasScanAnalyzer.excitation(result,cfg)
        self.assertEqual(metrics['excitation_voltage_vpp_v'],30)
        self.assertEqual(metrics['excitation_current_vpp_a'],10)
        result.overflow_channels=['C']
        self.assertIsNone(BiasScanAnalyzer.excitation(result,cfg)['excitation_current_vpp_a'])
        result.volts['B'][0]=np.nan
        self.assertIsNone(BiasScanAnalyzer.excitation(result,cfg)['excitation_voltage_vpp_v'])

    def test_config_and_enabled_channel_validation(self):
        cfg=BiasScanConfig(excitation_voltage_channel='B',excitation_current_channel='C')
        cfg.validate();acq=AcquisitionConfig(sample_rate_hz=500000)
        cfg.acquisition(acq)
        acq.channels['B'].enabled=False
        with self.assertRaisesRegex(ValueError,'未.*启用'):cfg.acquisition(acq)
        for bad in [replace(cfg,excitation_current_channel='B'),replace(cfg,aggregation='bad'),
                    replace(cfg,excitation_current_scale=0),replace(cfg,excitation_end_us=-1)]:
            with self.assertRaises(ValueError):bad.validate()

    def test_full_capture_save_and_progress(self):
        cfg=BiasScanConfig(stop_a=.5,repeats=5,interval_s=0,stable_hold_s=.01,poll_s=.01,cooldown_s=0,
            excitation_voltage_channel='B',excitation_current_channel='C',excitation_current_scale=2)
        states=[]
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
            job=BiasScanController(cfg,AcquisitionConfig(sample_rate_hz=500000),SimulatedPowerSupply(),FakePico(),folder,simulate=True,progress=states.append)
            result=job.run();self.assertEqual(result['status'],'complete',result['reason'])
            for summary in result['summary']:
                self.assertEqual(summary['statistics_count'],3)
                self.assertEqual(summary['excitation_voltage_statistics_count'],3)
                self.assertEqual(summary['excitation_current_statistics_count'],3)
                self.assertIsNotNone(summary['excitation_current_mean_a'])
            self.assertIn('excitation_current_vpp_a',(job.folder/'runs.csv').read_text(encoding='utf-8-sig'))
            self.assertEqual(json.loads((job.folder/'config.json').read_text(encoding='utf-8'))['configuration']['aggregation'],'trimmed_mean')
            self.assertEqual(states[-1]['progress_fraction'],1)
            self.assertEqual(states[-1]['captured_count'],10)
            self.assertEqual(states[-1]['estimated_remaining_s'],0)
            self.assertTrue(all(0<=s['progress_fraction']<=1 and s['elapsed_s']>=0 for s in states))
            self.assertGreaterEqual(result['elapsed_s'],0)
