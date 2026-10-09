"""User approved 20 A envelope; all tests use fake hardware."""
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from pico4824a.bias_scan import BiasScanConfig, BiasScanController, SimulatedPowerSupply
from pico4824a.config import AcquisitionConfig
from test_bias_scan import FakePico

ROOT=Path(__file__).resolve().parents[1]

class BiasCurrentRangeTests(unittest.TestCase):
    def test_twenty_amp_config_allowed_only_with_explicit_limit(self):
        with self.assertRaisesRegex(ValueError,'电流上限'):
            BiasScanConfig(stop_a=20).validate()
        cfg=BiasScanConfig(stop_a=20,actual_current_limit_a=20)
        cfg.validate()
        self.assertEqual(len(cfg.points()),41)
        self.assertEqual(cfg.points()[-1],20)
        self.assertEqual(BiasScanConfig().stop_a,6)
        self.assertEqual(BiasScanConfig().actual_current_limit_a,6)
        for values in [{'stop_a':20.1,'actual_current_limit_a':20},
                       {'actual_current_limit_a':20.1},{'actual_current_limit_a':0}]:
            with self.assertRaises(ValueError):BiasScanConfig(**values).validate()

    def test_twenty_amp_cooling_preserves_six_amp_reference(self):
        cfg=BiasScanConfig(stop_a=20,actual_current_limit_a=20,cooling_mode='current',cooldown_s=9,cooldown_min_s=1)
        cfg.validate()
        self.assertAlmostEqual(cfg.cooldown_for(20),100)
        self.assertAlmostEqual(cfg.cooldown_for(6),9)

    def test_complete_zero_to_twenty_simulated(self):
        cfg=BiasScanConfig(stop_a=20,step_a=.5,actual_current_limit_a=20,repeats=5,
            interval_s=0,stable_hold_s=.01,poll_s=.01,cooldown_s=0,voltage_limit_v=140)
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
            job=BiasScanController(cfg,AcquisitionConfig(sample_rate_hz=500000),
                SimulatedPowerSupply(),FakePico(),folder,simulate=True)
            result=job.run()
            self.assertEqual(result['status'],'complete',result['reason'])
            self.assertEqual(len(result['summary']),41)
            self.assertEqual(len(result['runs']),205)
            self.assertEqual(result['summary'][-1]['target_a'],20)
            self.assertEqual(result['output_state'],'off')

    def test_over_twenty_actual_current_still_trips(self):
        class Overcurrent(SimulatedPowerSupply):
            def read_actual(self):
                row=super().read_actual()
                if self.output_state=='on':row['current_a']=20.1
                return row
        cfg=BiasScanConfig(start_a=20,stop_a=20,actual_current_limit_a=20,repeats=5,
            interval_s=0,stable_hold_s=.01,poll_s=.01,cooldown_s=0,voltage_limit_v=140)
        with tempfile.TemporaryDirectory(dir=ROOT/'.test-tmp') as folder:
            job=BiasScanController(cfg,AcquisitionConfig(sample_rate_hz=500000),Overcurrent(),FakePico(),folder,simulate=True)
            result=job.run()
            self.assertEqual(result['status'],'error')
            self.assertEqual(result['output_state'],'off')
            self.assertIn('安全上限',result['reason'])
